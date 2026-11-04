"""Known local faults exercise failure capture without changing pinned workers.

Probe programs are test fixtures, never production failure-injection endpoints.
Exception messages identify observations; they do not establish a root cause.
"""

from __future__ import annotations

import json
import os
import signal
import struct
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from autocontext.execution import _process_group
from autocontext.weight_evolution import cpu_runner, cpu_worker
from autocontext.weight_evolution.artifacts import TensorInfo, file_identity, write_tensor_file
from autocontext.weight_evolution.cpu_failure_models import (
    CpuFailureContract,
    CpuFailureObservations,
    CpuFailureRecord,
)
from autocontext.weight_evolution.cpu_failure_record import build_cpu_failure_record, capture_observations
from autocontext.weight_evolution.cpu_models import CpuInferenceRequest, CpuModelContract

pytestmark = pytest.mark.skipif(sys.platform not in {"linux", "darwin"}, reason="trusted CPU faults require POSIX limits")


def failure_record(
    exception: BaseException, *, observations: CpuFailureObservations | None = None,
    contract: CpuFailureContract | None = None,
) -> CpuFailureRecord:
    return build_cpu_failure_record(
        contract or CpuFailureContract(), supervisor_digest="1" * 64, run_id="offline-fault",
        study_digest="2" * 64, runtime_digest="3" * 64, terminal_claim=None, terminal_result=None,
        exception=exception, observations=observations,
    )


def messages(record: CpuFailureRecord) -> list[str]:
    return [node.message.text for node in record.exceptions]


def assert_unknown(record: CpuFailureRecord) -> None:
    assert all(value is None for value in record.observations.to_dict().values())
    assert record.terminal_status == "absent"
    assert record.operation_kind is None and record.requests == () and record.checkpoints == ()
    assert record.contract.attribution_rule == "observed-exception-not-verified-root-cause"


def assert_reaped(pid: int) -> None:
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    with pytest.raises(ChildProcessError):
        os.waitpid(pid, os.WNOHANG)


def probe(source: str, *, wall_seconds: float = 3.0) -> tuple[bytes, bytes, float, int, int]:
    return cpu_runner._run_bounded([sys.executable, "-I", "-c", source], b"", wall_seconds=wall_seconds)


def response_failure(raw: bytes, *, elapsed: float, returncode: int) -> cpu_runner.CpuRunnerError:
    # These failures precede any request-dependent response validation.
    with pytest.raises(cpu_runner.CpuRunnerError) as caught:
        cpu_runner._validate_response(raw, (), runtime_digest="3" * 64, elapsed=elapsed, returncode=returncode)
    return caught.value


def test_real_wall_timeout_captures_message_without_inventing_measurements(tmp_path: Path) -> None:
    marker = tmp_path / "worker.pid"
    source = f"import os,pathlib,time;pathlib.Path({str(marker)!r}).write_text(str(os.getpid()));time.sleep(30)"
    with pytest.raises(cpu_runner.CpuRunnerError, match="wall deadline") as caught:
        probe(source, wall_seconds=1.0)
    assert_reaped(int(marker.read_text()))
    record = failure_record(caught.value)
    assert record.observation_kind == "live-exception"
    assert "wall deadline" in messages(record)[0]
    assert_unknown(record)


def test_nonzero_exit_message_does_not_become_a_measured_returncode() -> None:
    raw, stderr, elapsed, code, peak = probe("import sys;sys.exit(23)")
    assert code == 23 and raw == stderr == b"" and peak == 0
    error = response_failure(raw, elapsed=elapsed, returncode=code)
    implicit = failure_record(error)
    assert "(23)" in messages(implicit)[0]
    assert_unknown(implicit)
    explicit = failure_record(error, observations=capture_observations(
        CpuFailureContract(), returncode=code, elapsed_seconds=elapsed, stdout=raw, stderr=stderr,
    ))
    assert explicit.observations.returncode == 23 and explicit.observations.signal is None
    assert explicit.observations.elapsed_seconds == elapsed
    assert explicit.observations.peak_rss_bytes is None  # The runner's zero is an unavailable-monitor sentinel.
    assert explicit.observations.stdout is not None and explicit.observations.stdout.text == ""
    assert explicit.observations.stderr is not None and explicit.observations.stderr.text == ""


@pytest.mark.parametrize("raw", [b"not-json", b'{"schema_version":1,"schema_version":1}', b"\xff", b"NaN"])
def test_malformed_response_retains_explicit_and_suppressed_exception_links(raw: bytes) -> None:
    stdout, stderr, elapsed, code, _ = probe(f"import sys;sys.stdout.buffer.write({raw!r})")
    assert stdout == raw and stderr == b"" and code == 0
    record = failure_record(response_failure(stdout, elapsed=elapsed, returncode=code))
    root = record.exceptions[0]
    assert root.exception_type == "CpuRunnerError" and "invalid response envelope" in root.message.text
    assert root.cause_id is not None and root.context_id == root.cause_id and root.suppress_context is True
    assert len(record.exceptions) >= 2
    assert_unknown(record)


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_actual_pipe_overflow_is_bounded_and_does_not_guess_a_signal(tmp_path: Path, stream: str) -> None:
    marker = tmp_path / "worker.pid"
    source = (f"import os,pathlib,sys;pathlib.Path({str(marker)!r}).write_text(str(os.getpid()));"
              f"sys.{stream}.buffer.write(b'x'*1000000);sys.{stream}.flush()")
    with pytest.raises(cpu_runner.CpuRunnerError, match=f"{stream} exceeds byte cap") as caught:
        probe(source)
    assert_reaped(int(marker.read_text()))
    record = failure_record(caught.value)
    assert f"{stream} exceeds byte cap" in messages(record)[0]
    assert_unknown(record)


def test_real_stderr_observations_are_redacted_truncated_and_utf8_bounded() -> None:
    secret = "local-fixture-secret"
    payload = secret.encode() + b"\xff\n" + ("\u03bb" * 3000).encode()
    raw, stderr, elapsed, code, peak = probe(
        f"import sys;sys.stdout.buffer.write(b'not-json');sys.stderr.buffer.write({payload!r})",
    )
    assert stderr == payload and code == 0 and peak == 0
    contract = CpuFailureContract(max_stream_bytes=64)
    record = failure_record(response_failure(raw, elapsed=elapsed, returncode=code), contract=contract,
                            observations=capture_observations(contract, returncode=code, elapsed_seconds=elapsed,
                                                              stdout=raw, stderr=stderr, redact=(secret,)))
    observed = record.observations.stderr
    assert observed is not None and len(observed.text.encode()) <= 64
    assert observed.truncated and observed.redacted and observed.encoding_replaced
    assert secret not in json.dumps(record.to_dict())
    assert record.observations.peak_rss_bytes is None and record.observations.signal is None


def invalid_checkpoint_request(tmp_path: Path) -> tuple[CpuInferenceRequest, Path]:
    checkpoint = tmp_path / "wrong-shape.safetensors"
    payloads = {"projection.bias": struct.pack("<ff", 0.0, 0.0),
                "projection.weight": struct.pack("<ffffff", 1.0, 0.0, 0.0, 1.0, 0.0, 0.0)}
    write_tensor_file(checkpoint, (TensorInfo("projection.bias", "F32", (2,), 0, 8),
                                  TensorInfo("projection.weight", "F32", (2, 3), 8, 32)), payloads.__getitem__)
    identity = file_identity(checkpoint)
    request = CpuInferenceRequest(request_id="wrong-shape", model=CpuModelContract(vocabulary=("red", "blue"),
                                                                                 output_tokens=("yes", "no")),
                                  checkpoint_sha256=identity["sha256"], checkpoint_size_bytes=identity["size_bytes"],
                                  prompt="red")
    return request, checkpoint


def test_fixed_worker_stderr_error_does_not_pretend_its_raw_stream_was_observed(tmp_path: Path) -> None:
    request, checkpoint = invalid_checkpoint_request(tmp_path)
    with pytest.raises(cpu_runner.CpuRunnerError, match="reported stderr") as caught:
        cpu_runner.run_cpu_batch((request,), checkpoint_path=checkpoint)
    record = failure_record(caught.value)
    assert "CPU worker reported stderr:" in messages(record)[0]
    assert_unknown(record)


def test_monitor_precheck_failure_is_captured_without_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    request, checkpoint = invalid_checkpoint_request(tmp_path)

    def unavailable(pid: int) -> int:
        raise cpu_runner.CpuRunnerError("fixture Darwin monitoring unavailable before dispatch")

    with monkeypatch.context() as patch:
        patch.setattr(cpu_runner.sys, "platform", "darwin")
        patch.setattr(cpu_runner, "_darwin_rss", unavailable)
        patch.setattr(cpu_runner.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("precheck dispatched"))
        with pytest.raises(cpu_runner.CpuRunnerError, match="monitoring unavailable") as caught:
            cpu_runner.run_cpu_batch((request,), checkpoint_path=checkpoint)
    record = failure_record(caught.value)
    assert "before dispatch" in messages(record)[0]
    assert_unknown(record)


def test_monitor_failure_with_live_leader_stops_and_reaps_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    pids: list[int] = []

    def unavailable(pid: int) -> int:
        pids.append(pid)
        raise cpu_runner.CpuRunnerError("fixture live Darwin monitor unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(cpu_runner.sys, "platform", "darwin")
        patch.setattr(cpu_runner, "_darwin_rss", unavailable)
        patch.setattr(_process_group, "_darwin_leader_is_exited", lambda pid: False)
        with pytest.raises(cpu_runner.CpuRunnerError, match="monitor unavailable") as caught:
            cpu_runner._run_bounded([sys.executable, "-I", "-c", "import time;time.sleep(30)"], b"",
                                    wall_seconds=3.0, memory_bytes=512 * 1024 * 1024)
    assert len(pids) == 1
    assert_reaped(pids[0])
    record = failure_record(caught.value)
    assert "live Darwin monitor unavailable" in messages(record)[0]
    assert_unknown(record)


def test_exit_during_monitor_recheck_does_not_invent_monitor_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    pids: list[int] = []

    def unavailable(pid: int) -> int:
        pids.append(pid)
        raise cpu_runner.CpuRunnerError("fixture stale monitor observation")

    def await_actual_exit(pid: int) -> bool:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None:
                return True
            time.sleep(0.001)
        raise AssertionError("bounded fixture leader did not exit")

    with monkeypatch.context() as patch:
        patch.setattr(cpu_runner.sys, "platform", "darwin")
        patch.setattr(cpu_runner, "_darwin_rss", unavailable)
        patch.setattr(_process_group, "_darwin_leader_is_exited", await_actual_exit)
        raw, stderr, elapsed, code, peak = cpu_runner._run_bounded(
            [sys.executable, "-I", "-c", "import sys,time;time.sleep(.05);sys.exit(23)"], b"",
            wall_seconds=3.0, memory_bytes=512 * 1024 * 1024,
        )
    assert pids and code == 23 and peak == 0 and raw == stderr == b""
    assert_reaped(pids[0])
    record = failure_record(response_failure(raw, elapsed=elapsed, returncode=code))
    assert "unsuccessfully (23)" in messages(record)[0]
    assert not any("stale monitor" in message for message in messages(record))
    assert_unknown(record)


def test_cleanup_masking_preserves_primary_context_with_bounded_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    original = cpu_runner._kill_and_reap
    pids: list[int] = []

    def fail_after_real_cleanup(process: Any) -> None:
        original(process)
        pids.append(process.pid)
        raise cpu_runner.CpuRunnerError("fixture cleanup failed after real reap " + "\u03bb" * 3000)

    monkeypatch.setattr(cpu_runner, "_kill_and_reap", fail_after_real_cleanup)
    with pytest.raises(cpu_runner.CpuRunnerError, match="cleanup failed") as caught:
        probe("import time;time.sleep(30)", wall_seconds=0.2)
    assert len(pids) == 1
    assert_reaped(pids[0])
    record = failure_record(caught.value)
    root = record.exceptions[0]
    assert root.context_id is not None and root.message.truncated
    assert len(root.message.text.encode()) <= record.contract.max_message_bytes
    assert "wall deadline" in record.exceptions[root.context_id].message.text
    assert_unknown(record)
    bounded = failure_record(caught.value, contract=CpuFailureContract(max_exception_nodes=1))
    assert len(bounded.exceptions) == 1 and bounded.exception_chain_truncated
    assert bounded.exceptions[0].context_truncated and bounded.exceptions[0].context_id is None


def resource_probe_source(body: str) -> str:
    worker_path = str(Path(cpu_worker.__file__).resolve())
    return f"""
import importlib.util, json, os, resource, sys
spec = importlib.util.spec_from_file_location('trusted_cpu_worker', {worker_path!r})
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
memory = worker._install_resource_limits(256, 1)
print(json.dumps({{'pid': os.getpid(), 'memory': memory,
                  'as': resource.getrlimit(resource.RLIMIT_AS),
                  'cpu': resource.getrlimit(resource.RLIMIT_CPU),
                  'core': resource.getrlimit(resource.RLIMIT_CORE)}}), flush=True)
{body}
"""


@pytest.mark.skipif(sys.platform != "linux", reason="Linux hard address-space allocation failure")
def test_linux_allocation_denial_records_observed_exit_without_claiming_memory_kill() -> None:
    source = resource_probe_source("""
try:
    allocation = bytearray(512 * 1024 * 1024)
except MemoryError:
    sys.stderr.write('FIXTURE_AS_ALLOCATION_DENIAL')
    sys.exit(73)
raise AssertionError('allocation exceeded installed address-space limit')
""")
    raw, stderr, elapsed, code, peak = probe(source, wall_seconds=10.0)
    ready = json.loads(raw)
    assert ready["as"] == [256 * 1024 * 1024] * 2 and ready["cpu"] == [1, 1] and ready["core"] == [0, 0]
    assert code == 73 and stderr == b"FIXTURE_AS_ALLOCATION_DENIAL" and peak == 0
    assert_reaped(ready["pid"])
    record = failure_record(response_failure(raw, elapsed=elapsed, returncode=code), observations=capture_observations(
        CpuFailureContract(), returncode=code, elapsed_seconds=elapsed, stdout=raw, stderr=stderr,
    ))
    assert record.observations.returncode == 73 and record.observations.signal is None
    assert record.observations.peak_rss_bytes is None
    assert "exited unsuccessfully" in messages(record)[0]


@pytest.mark.skipif(sys.platform != "linux", reason="Linux hard CPU limit with independent wall watchdog")
def test_linux_cpu_limit_records_signal_observation_without_inferring_root_cause() -> None:
    raw, stderr, elapsed, code, peak = probe(resource_probe_source("while True:\n    pass"), wall_seconds=10.0)
    ready = json.loads(raw)
    assert ready["cpu"] == [1, 1] and ready["core"] == [0, 0]
    assert code == -signal.SIGKILL and stderr == b"" and peak == 0
    assert 0 < elapsed < 10.0
    assert_reaped(ready["pid"])
    error = response_failure(raw, elapsed=elapsed, returncode=code)
    implicit = failure_record(error)
    assert_unknown(implicit)
    explicit = failure_record(error, observations=capture_observations(
        CpuFailureContract(), returncode=code, signal=int(signal.SIGKILL), elapsed_seconds=elapsed,
        stdout=raw, stderr=stderr,
    ))
    assert explicit.observations.signal == signal.SIGKILL and explicit.observations.returncode == -signal.SIGKILL
    assert explicit.observations.peak_rss_bytes is None
    assert explicit.contract.attribution_rule == "observed-exception-not-verified-root-cause"
