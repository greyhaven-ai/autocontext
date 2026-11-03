from __future__ import annotations

import json
import os
import signal
import struct
import sys
from pathlib import Path
from typing import Any

import pytest

from autocontext.weight_evolution import cpu_runner
from autocontext.weight_evolution.artifacts import TensorInfo, canonical_digest, file_identity, write_tensor_file
from autocontext.weight_evolution.cpu_models import CpuInferenceRequest, CpuModelContract
from autocontext.weight_evolution.cpu_runner import CpuRunnerError, run_cpu_batch, runtime_identity
from autocontext.weight_evolution.cpu_worker import _audit_guard


@pytest.fixture
def input_case(tmp_path: Path) -> tuple[CpuInferenceRequest, Path]:
    checkpoint = tmp_path / "model.safetensors"
    payloads = {"projection.bias": struct.pack("<ff", 0.0, 0.0),
                "projection.weight": struct.pack("<ffff", 2.0, -1.0, -1.0, 2.0)}
    write_tensor_file(checkpoint, (TensorInfo("projection.bias", "F32", (2,), 0, 8),
                                   TensorInfo("projection.weight", "F32", (2, 2), 8, 24)), payloads.__getitem__)
    identity = file_identity(checkpoint)
    request = CpuInferenceRequest(request_id="case-one", model=CpuModelContract(vocabulary=("red", "blue"),
                                                                               output_tokens=("yes", "no")),
                                  checkpoint_sha256=identity["sha256"], checkpoint_size_bytes=identity["size_bytes"],
                                  prompt="red")
    return request, checkpoint


def test_real_cpu_worker_and_semantic_digest(input_case: tuple[CpuInferenceRequest, Path]) -> None:
    request, checkpoint = input_case
    first = run_cpu_batch((request,), checkpoint_path=checkpoint)
    second = run_cpu_batch((request,), checkpoint_path=checkpoint)
    assert first.results[0].text == "yes"
    assert first.results[0].logits == (2.0, -1.0)
    assert first.results[0].input_tokens == (0,)
    assert first.device == "cpu" and first.model_execution_performed is True
    assert first.gpu_execution_performed is False
    assert first.semantic_digest == second.semantic_digest
    assert first.telemetry.elapsed_seconds > 0 and first.telemetry.peak_rss_bytes > 0
    assert first.telemetry.memory_limit_bytes == 512 * 1024 * 1024
    if sys.platform == "linux":
        assert first.telemetry.memory_enforcement == "hard_address_space"
        assert first.telemetry.address_space_limit_bytes == 512 * 1024 * 1024
        assert first.telemetry.rss_sampling_may_overshoot is False
    else:
        assert first.telemetry.memory_enforcement == "monitored_rss"
        assert first.telemetry.address_space_limit_bytes is None
        assert first.telemetry.rss_sampling_may_overshoot is True
    assert first.runtime_digest == canonical_digest(runtime_identity())


@pytest.mark.parametrize("mutation", ["batch", "request", "checkpoint", "runtime", "count", "token", "text",
                                      "choice", "memory", "bool"])
def test_tampered_worker_responses_rejected(input_case: tuple[CpuInferenceRequest, Path], mutation: str) -> None:
    request, checkpoint = input_case
    good = run_cpu_batch((request,), checkpoint_path=checkpoint)
    value = good.to_dict()
    if mutation in {"batch", "runtime"}:
        value[f"{mutation}_digest"] = "0" * 64
    elif mutation == "request":
        value["results"][0]["request_digest"] = "0" * 64
    elif mutation == "checkpoint":
        value["results"][0]["checkpoint_sha256"] = "0" * 64
    elif mutation == "count":
        value["results"].append(value["results"][0])
    elif mutation == "token":
        value["results"][0]["input_tokens"] = [1]
    elif mutation == "text":
        value["results"][0]["text"] = "no"
    elif mutation == "choice":
        value["results"][0]["output_token_id"] = 1
        value["results"][0]["text"] = "no"
    elif mutation == "memory":
        value["telemetry"]["memory_limit_bytes"] += 1
    else:
        value["model_execution_performed"] = 1
    with pytest.raises(CpuRunnerError, match="response|result|resource|count"):
        cpu_runner._validate_response(json.dumps(value).encode(), (request,), runtime_digest=good.runtime_digest,
                                      elapsed=0.5, returncode=0)


def test_duplicate_json_and_nonzero_exit_rejected(input_case: tuple[CpuInferenceRequest, Path]) -> None:
    request, _ = input_case
    with pytest.raises(CpuRunnerError, match="invalid response"):
        cpu_runner._validate_response(b'{"schema_version":1,"schema_version":1}', (request,),
                                      runtime_digest="0" * 64, elapsed=0.1, returncode=0)
    with pytest.raises(CpuRunnerError, match="unsuccessfully"):
        cpu_runner._validate_response(b"", (request,), runtime_digest="0" * 64, elapsed=0.1, returncode=1)


def test_batch_bounds_and_mismatches_fail_before_dispatch(
    input_case: tuple[CpuInferenceRequest, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, checkpoint = input_case

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("invalid batch dispatched")

    monkeypatch.setattr(cpu_runner, "_run_bounded", forbidden)
    for requests in ((), (request,) * 33, (request, request),
                     (request, request.model_copy(update={"request_id": "different", "checkpoint_sha256": "0" * 64}))):
        with pytest.raises(CpuRunnerError):
            run_cpu_batch(requests, checkpoint_path=checkpoint)
    checkpoint.write_bytes(checkpoint.read_bytes() + b"corrupt")
    with pytest.raises(ValueError):
        run_cpu_batch((request,), checkpoint_path=checkpoint)


def test_unsupported_platform_rejected_before_dispatch(
    input_case: tuple[CpuInferenceRequest, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, checkpoint = input_case
    monkeypatch.setattr(cpu_runner.sys, "platform", "unsupported")
    with pytest.raises(CpuRunnerError, match="supported Linux or Darwin"):
        run_cpu_batch((request,), checkpoint_path=checkpoint)


def test_timeout_kills_and_reaps_worker(tmp_path: Path) -> None:
    marker = tmp_path / "worker.pid"
    script = f"import os,time,pathlib;pathlib.Path({str(marker)!r}).write_text(str(os.getpid()));time.sleep(30)"
    with pytest.raises(CpuRunnerError, match="wall deadline"):
        cpu_runner._run_bounded([sys.executable, "-I", "-c", script], b"", wall_seconds=0.3)
    pid = int(marker.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    with pytest.raises(ChildProcessError):
        os.waitpid(pid, os.WNOHANG)


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_pipe_output_caps_terminate_worker(stream: str) -> None:
    script = f"import sys;sys.{stream}.buffer.write(b'x'*1000000);sys.{stream}.flush()"
    with pytest.raises(CpuRunnerError, match=f"{stream} exceeds byte cap"):
        cpu_runner._run_bounded([sys.executable, "-I", "-c", script], b"", wall_seconds=3.0)


def test_input_cap_before_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cpu_runner.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("oversized input dispatched"))
    with pytest.raises(CpuRunnerError, match="request exceeds byte cap"):
        cpu_runner._run_bounded([], b"x" * (cpu_runner.MAX_REQUEST_BYTES + 1), wall_seconds=1.0)


def test_worker_environment_disables_devices_and_omits_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "private-test-secret")
    script = "import json,os; print(json.dumps(dict(os.environ)))"
    raw, error, _, code, _ = cpu_runner._run_bounded([sys.executable, "-I", "-c", script], b"", wall_seconds=3.0)
    environment = json.loads(raw)
    assert code == 0 and error == b""
    assert "OPENAI_API_KEY" not in environment
    assert environment["CUDA_VISIBLE_DEVICES"] == environment["HIP_VISIBLE_DEVICES"] == ""
    assert environment["NVIDIA_VISIBLE_DEVICES"] == "void"
    assert environment["PYTORCH_ENABLE_MPS_FALLBACK"] == "0"


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin RSS monitor")
def test_rss_limit_and_unavailable_monitor_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cpu_runner, "_darwin_rss", lambda pid: 1024)
    with pytest.raises(CpuRunnerError, match="monitored RSS"):
        cpu_runner._run_bounded([sys.executable, "-I", "-c", "import time;time.sleep(30)"], b"",
                                wall_seconds=2.0, memory_bytes=512)

    def failed(pid: int) -> int:
        raise CpuRunnerError("RSS monitoring is unavailable")

    monkeypatch.setattr(cpu_runner, "_darwin_rss", failed)
    with pytest.raises(CpuRunnerError, match="monitoring is unavailable"):
        cpu_runner._require_supported_host()


def test_request_contract_has_no_scorer_or_gold(input_case: tuple[CpuInferenceRequest, Path]) -> None:
    request, _ = input_case
    for field in ("gold", "scorer", "expected_output", "device", "code"):
        with pytest.raises(ValueError):
            CpuInferenceRequest.model_validate_json(json.dumps({**request.to_dict(), field: "forbidden"}))


def test_saved_receipt_verification_requires_no_worker(
    input_case: tuple[CpuInferenceRequest, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, checkpoint = input_case
    receipt = run_cpu_batch((request,), checkpoint_path=checkpoint)
    monkeypatch.setattr(cpu_runner, "_run_bounded", lambda *args, **kwargs: pytest.fail("receipt validation dispatched"))
    assert cpu_runner.validate_cpu_receipt(receipt, (request,), receipt.runtime_digest) == receipt
    invalid = receipt.model_copy(update={"telemetry": receipt.telemetry.model_copy(update={"elapsed_seconds": 999.0})})
    with pytest.raises(CpuRunnerError, match="wall deadline"):
        cpu_runner.validate_cpu_receipt(invalid, (request,), receipt.runtime_digest)


@pytest.mark.parametrize("event,arguments", [("socket.__new__", ()), ("socket.getaddrinfo", ()),
                                             ("subprocess.Popen", ()), ("os.fork", ()),
                                             ("import", ("torch",)), ("import", ("mlx.core",)),
                                             ("import", ("numpy",))])
def test_worker_guard_disallows_external_execution(event: str, arguments: tuple[object, ...]) -> None:
    with pytest.raises(RuntimeError, match="CPU worker"):
        _audit_guard(event, arguments)


def test_worker_audit_hook_blocks_real_socket_creation() -> None:
    source = str(Path(cpu_runner.__file__).resolve().parents[2])
    script = (f"import sys;sys.path.insert(0,{source!r}); "
              "from autocontext.weight_evolution.cpu_worker import _audit_guard; "
              "sys.addaudithook(_audit_guard);import socket;socket.socket()")
    _, error, _, code, _ = cpu_runner._run_bounded([sys.executable, "-I", "-c", script], b"", wall_seconds=3.0)
    assert code != 0
    assert b"CPU worker forbids network" in error


def test_process_group_cleanup_includes_descendant(tmp_path: Path) -> None:
    marker = tmp_path / "child.pid"
    # SIGKILL cleanup is verified by the descendant's stopped heartbeat.
    heartbeat = tmp_path / "heartbeat"
    child = (f"import pathlib,time; p=pathlib.Path({str(heartbeat)!r}); "
             "[(p.write_text(str(i)),time.sleep(.01)) for i in range(500)]")
    script = ("import subprocess,sys,pathlib,time; "
              f"p=subprocess.Popen([sys.executable,'-I','-c',{child!r}]); "
              f"pathlib.Path({str(marker)!r}).write_text(str(p.pid)); time.sleep(30)")
    with pytest.raises(CpuRunnerError, match="wall deadline"):
        cpu_runner._run_bounded([sys.executable, "-I", "-c", script], b"", wall_seconds=0.5)
    # A killed orphan can briefly remain a zombie, so kill(pid,0) is not a
    # portable liveness check. A resumed SIGCONT cannot revive its heartbeat.
    import time

    before = heartbeat.read_bytes()
    try:
        os.kill(int(marker.read_text()), signal.SIGCONT)
    except ProcessLookupError:
        pass
    time.sleep(0.05)
    assert heartbeat.read_bytes() == before
