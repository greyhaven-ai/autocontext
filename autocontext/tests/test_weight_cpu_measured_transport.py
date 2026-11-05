"""Deterministic local probes for the private measured CPU process boundary."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from autocontext.weight_evolution import cpu_measured_transport as transport
from autocontext.weight_evolution import cpu_runner, cpu_worker

pytestmark = pytest.mark.skipif(sys.platform not in {"linux", "darwin"}, reason="measured CPU transport requires POSIX")
MEMORY = 512 * 1024 * 1024


def probe(source: str, *, wall: float = 3.0, payload: bytes = b"", memory: int = MEMORY) -> transport.MeasuredTransportResult:
    return transport._run_measured([sys.executable, "-I", "-c", source], payload,
                                   wall_seconds=wall, memory_bytes=memory)


def reaped(pid: int) -> None:
    with pytest.raises(ChildProcessError):
        os.waitpid(pid, os.WNOHANG)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_success_preserves_capped_raw_streams_and_isolated_environment() -> None:
    result = probe("import json,os,sys; print(json.dumps({'pid':os.getpid(),'env':dict(os.environ),"
                   "'cwd':os.getcwd(),'input':sys.stdin.buffer.read().hex()}));sys.stderr.write('fixture')",
                   payload=b"\x00bounded-input")
    assert result.primary_error is result.cleanup_error is None
    assert result.stdout is not None
    value = json.loads(result.stdout)
    assert value["input"] == b"\x00bounded-input".hex() and result.stderr == b"fixture"
    assert value["env"]["CUDA_VISIBLE_DEVICES"] == value["env"]["HIP_VISIBLE_DEVICES"] == ""
    assert value["env"]["NVIDIA_VISIBLE_DEVICES"] == "void"
    assert Path(value["cwd"]).resolve() == Path(value["env"]["HOME"]).resolve() == Path(value["env"]["TMPDIR"]).resolve()
    assert not Path(value["cwd"]).exists()
    observation = result.observation
    assert observation.trigger == "completed" and observation.dispatched
    assert observation.state_before_cleanup == "exited"
    assert observation.returncode_before_cleanup == observation.returncode_after_cleanup == 0
    assert observation.leader_reaped is True and observation.cleanup_state == "completed"
    assert observation.total_seconds is not None and observation.elapsed_to_trigger_seconds is not None
    assert 0 < observation.elapsed_to_trigger_seconds <= observation.total_seconds
    if sys.platform == "linux":
        assert observation.peak_rss_bytes is observation.rss_source is None
    reaped(value["pid"])


def test_nonzero_code_is_observed_before_cleanup() -> None:
    result = probe("import os,sys;print(os.getpid());sys.stderr.write('specific failure');sys.exit(23)")
    observation = result.observation
    assert observation.trigger == "nonzero-exit" and result.primary_error is not None
    assert result.cleanup_error is None and result.stderr == b"specific failure"
    assert observation.returncode_before_cleanup == observation.returncode_after_cleanup == 23
    assert observation.state_before_cleanup == "exited" and observation.leader_reaped
    assert result.stdout is not None
    reaped(int(result.stdout))


def test_wall_trigger_keeps_precleanup_status_distinct_from_parent_kill() -> None:
    result = probe("import os,time;print(os.getpid(),flush=True);time.sleep(30)", wall=0.2)
    observation = result.observation
    assert observation.trigger == "wall-deadline" and result.primary_error is not None
    assert observation.state_before_cleanup == "running" and observation.returncode_before_cleanup is None
    assert observation.returncode_after_cleanup == -signal.SIGKILL
    assert observation.signal_attempted and observation.signal_sent and observation.signal_number == 9
    assert observation.cleanup_state == "completed" and observation.leader_reaped
    assert result.stdout is not None
    reaped(int(result.stdout))
    assert "oom" not in json.dumps(observation.to_dict()).lower()


@pytest.mark.parametrize("stream,cap", [("stdout", cpu_runner.MAX_STDOUT_BYTES), ("stderr", cpu_runner.MAX_STDERR_BYTES)])
def test_pipe_overflow_retains_only_the_bounded_prefix(stream: str, cap: int) -> None:
    result = probe(f"import sys;sys.{stream}.buffer.write(b'x'*1000000);sys.{stream}.flush()")
    assert result.observation.trigger == f"{stream}-cap"
    assert getattr(result, stream) == b"x" * cap
    assert getattr(result.observation, stream + "_capped") is True
    assert result.primary_error is not None and result.cleanup_error is None
    assert result.observation.leader_reaped is True


@pytest.mark.parametrize("raw", [b"not-json", b"\xff", b'{"x":1,"x":2}', b"NaN"])
def test_malformed_response_keeps_completed_transport_observations(raw: bytes) -> None:
    result = probe(f"import sys;sys.stdout.buffer.write({raw!r})")
    assert result.primary_error is result.cleanup_error is None
    assert result.stdout == raw and result.observation.trigger == "completed"
    with pytest.raises(cpu_runner.CpuRunnerError, match="invalid response"):
        cpu_runner._validate_response(raw, (), runtime_digest="a" * 64,
                                      elapsed=result.observation.total_seconds or 0, returncode=0)
    assert result.observation.returncode_before_cleanup == result.observation.returncode_after_cleanup == 0


def test_unreturned_launch_failure_propagates_without_false_nondispatch_record() -> None:
    with pytest.raises(FileNotFoundError):
        transport._run_measured(["/nonexistent/measured-cpu-fixture"], b"", wall_seconds=1.0, memory_bytes=MEMORY)


def test_unreturned_constructor_interruption_can_follow_real_child_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    original = transport.subprocess.Popen
    pids: list[int] = []
    fault = KeyboardInterrupt("fixture interruption after child launch, before handle return")

    def interrupted(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        process = original(*args, **kwargs)
        pids.append(process.pid)
        # The fixture owns this handle and performs cleanup; production never
        # receives it and therefore must not claim dispatch=False or signal a PID.
        process.wait(timeout=3)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        raise fault

    monkeypatch.setattr(transport.subprocess, "Popen", interrupted)
    monkeypatch.setattr(transport, "signal_owned_process_group", lambda *a: pytest.fail("unowned launch signaled"))
    with pytest.raises(KeyboardInterrupt) as caught:
        probe("pass")
    assert caught.value is fault and pids
    reaped(pids[0])


@pytest.mark.parametrize("changes", [
    {"payload": b"x" * (cpu_runner.MAX_REQUEST_BYTES + 1)}, {"wall_seconds": float("nan")},
    {"wall_seconds": True}, {"wall_seconds": 31.0}, {"memory_bytes": True},
    {"memory_bytes": 1024}, {"command": []},
])
def test_invalid_boundary_inputs_fail_before_dispatch(changes: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transport.subprocess, "Popen", lambda *a, **k: pytest.fail("invalid request dispatched"))
    values = {"command": [sys.executable], "payload": b"", "wall_seconds": 1.0, "memory_bytes": MEMORY} | changes
    result = transport._run_measured(**values)
    assert result.observation.trigger == "input-rejected" and not result.observation.dispatched
    assert result.primary_error is not None and result.stdout is result.stderr is None


def test_preflight_failure_has_no_child_or_cleanup_observations(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable() -> None:
        raise cpu_runner.CpuRunnerError("fixture monitor unavailable before dispatch")

    monkeypatch.setattr(cpu_runner, "_require_supported_host", unavailable)
    result = probe("raise AssertionError('should not run')")
    assert result.observation.trigger == "preflight-failed" and not result.observation.dispatched
    assert result.observation.cleanup_state == "not-needed" and result.observation.leader_reaped is None


def test_primary_and_cleanup_errors_survive_independently(monkeypatch: pytest.MonkeyPatch) -> None:
    original = transport.signal_owned_process_group

    def kill_then_fail(pid: int, signum: int) -> bool:
        original(pid, signum)
        raise OSError("fixture cleanup observation failed after actual signal")

    monkeypatch.setattr(transport, "signal_owned_process_group", kill_then_fail)
    result = probe("import os,time;print(os.getpid(),flush=True);time.sleep(30)", wall=0.2)
    assert result.primary_error is not None and "wall deadline" in str(result.primary_error)
    assert isinstance(result.cleanup_error, OSError) and "fixture cleanup" in str(result.cleanup_error)
    assert result.observation.trigger == "wall-deadline" and result.observation.cleanup_state == "failed"
    assert result.observation.signal_attempted and result.observation.signal_sent is None
    assert result.observation.returncode_after_cleanup == -signal.SIGKILL and result.observation.leader_reaped
    assert result.stdout is not None
    reaped(int(result.stdout))


def test_parent_temporary_cleanup_failure_is_measured_without_child(monkeypatch: pytest.MonkeyPatch) -> None:
    original = transport.tempfile.TemporaryDirectory

    class TemporaryFixture:
        def __init__(self, **kwargs: Any) -> None:
            self.temporary = original(**kwargs)

        @property
        def name(self) -> str:
            raise OSError("fixture prelaunch environment failure")

        def cleanup(self) -> None:
            self.temporary.cleanup()
            raise OSError("fixture parent temporary cleanup failed")

    monkeypatch.setattr(transport.tempfile, "TemporaryDirectory", TemporaryFixture)
    monkeypatch.setattr(transport.subprocess, "Popen", lambda *a, **k: pytest.fail("prelaunch failure dispatched"))
    result = probe("pass")
    assert isinstance(result.primary_error, OSError) and isinstance(result.cleanup_error, OSError)
    assert result.observation.trigger == "preflight-failed"
    assert not result.observation.dispatched and result.observation.cleanup_state == "failed"
    assert result.observation.cleanup_seconds is not None and result.observation.returncode_after_cleanup is None


def test_temporary_cleanup_cannot_mask_unreturned_launch_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    original = transport.tempfile.TemporaryDirectory.cleanup
    fault = FileNotFoundError("fixture unreturned launch")

    def cleanup_then_fail(self: Any) -> None:
        original(self)
        raise OSError("fixture temporary cleanup failed")

    def fail_launch(*args: Any, **kwargs: Any) -> Any:
        raise fault

    monkeypatch.setattr(transport.tempfile.TemporaryDirectory, "cleanup", cleanup_then_fail)
    monkeypatch.setattr(transport.subprocess, "Popen", fail_launch)
    with pytest.raises(FileNotFoundError) as caught:
        probe("pass")
    assert caught.value is fault


def test_additional_cleanup_failures_are_counted_without_invented_exception_links(monkeypatch: pytest.MonkeyPatch) -> None:
    signal_original = transport.signal_owned_process_group
    cleanup_original = transport.tempfile.TemporaryDirectory.cleanup

    def kill_then_fail(pid: int, signum: int) -> bool:
        signal_original(pid, signum)
        raise OSError("first cleanup fault")

    def cleanup_then_fail(self: Any) -> None:
        cleanup_original(self)
        raise OSError("second cleanup fault")

    monkeypatch.setattr(transport, "signal_owned_process_group", kill_then_fail)
    monkeypatch.setattr(transport.tempfile.TemporaryDirectory, "cleanup", cleanup_then_fail)
    result = probe("pass")
    assert result.primary_error is None and str(result.cleanup_error) == "first cleanup fault"
    assert result.observation.additional_cleanup_errors == 1 and result.observation.trigger == "cleanup-failed"
    assert result.observation.leader_reaped


def test_lost_child_ownership_never_signals_a_recycled_group(monkeypatch: pytest.MonkeyPatch) -> None:
    original = transport._observe_leader
    pids: list[int] = []

    def reap_elsewhere(process: subprocess.Popen[bytes]) -> tuple[transport.ProcessState, int | None]:
        if not pids:
            pids.append(process.pid)
            process.wait(timeout=3)
        return original(process)

    monkeypatch.setattr(transport, "_observe_leader", reap_elsewhere)
    monkeypatch.setattr(transport, "signal_owned_process_group", lambda *a: pytest.fail("lost ownership signaled"))
    result = probe("pass")
    observation = result.observation
    assert observation.trigger == "ownership-lost" and observation.cleanup_state == "ownership-lost"
    assert observation.state_before_cleanup == "ownership-lost" and not observation.signal_attempted
    assert observation.returncode_before_cleanup is observation.returncode_after_cleanup is observation.leader_reaped is None
    reaped(pids[0])


def test_waitid_snapshot_does_not_reap_the_exit_status() -> None:
    process = subprocess.Popen([sys.executable, "-I", "-c", "raise SystemExit(23)"], start_new_session=True)
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            state, code = transport._observe_leader(process)
            if state == "exited":
                break
            time.sleep(0.001)
        assert (state, code) == ("exited", 23) and process.returncode is None
        assert transport._observe_leader(process) == ("exited", 23)
        assert process.wait(timeout=3) == 23
    finally:
        if process.returncode is None:
            process.kill()
            process.wait(timeout=3)


def test_live_monitor_unavailable_retains_null_rss_and_reaps(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(pid: int) -> int:
        raise cpu_runner.CpuRunnerError("fixture live monitor unavailable")

    monkeypatch.setattr(cpu_runner, "_require_supported_host", lambda: None)
    monkeypatch.setattr(transport.sys, "platform", "darwin")
    monkeypatch.setattr(cpu_runner, "_darwin_rss", unavailable)
    result = probe("import time;time.sleep(30)")
    assert result.observation.trigger == "rss-monitor-unavailable" and result.observation.leader_reaped
    assert result.observation.peak_rss_bytes is result.observation.rss_source is None
    assert result.observation.returncode_before_cleanup is None
    assert result.observation.returncode_after_cleanup == -signal.SIGKILL


def test_sampled_rss_limit_records_parent_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cpu_runner, "_require_supported_host", lambda: None)
    monkeypatch.setattr(transport.sys, "platform", "darwin")
    monkeypatch.setattr(cpu_runner, "_darwin_rss", lambda pid: MEMORY + 1)
    result = probe("import time;time.sleep(30)")
    assert result.observation.trigger == "rss-limit" and result.observation.leader_reaped
    assert result.observation.peak_rss_bytes == MEMORY + 1
    assert result.observation.rss_source == "parent-darwin-proc_pidinfo"
    assert result.observation.returncode_before_cleanup is None


def test_monitor_exit_race_retains_exit_without_fabricated_rss(monkeypatch: pytest.MonkeyPatch) -> None:
    def exit_then_unavailable(pid: int) -> int:
        waitid: Any = getattr(os, "waitid", None)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None:
                raise cpu_runner.CpuRunnerError("fixture exited before RSS sample")
            time.sleep(0.001)
        raise AssertionError("fixture did not exit before bounded recheck")

    monkeypatch.setattr(cpu_runner, "_require_supported_host", lambda: None)
    monkeypatch.setattr(transport.sys, "platform", "darwin")
    monkeypatch.setattr(cpu_runner, "_darwin_rss", exit_then_unavailable)
    result = probe("import time;time.sleep(.05)")
    assert result.primary_error is result.cleanup_error is None
    assert result.observation.trigger == "completed" and result.observation.leader_reaped
    assert result.observation.peak_rss_bytes is result.observation.rss_source is None


def test_monitor_exit_visibility_lag_uses_native_bounded_grace_then_observes_actual_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_waitid: Any = getattr(os, "waitid", None)
    original_grace = transport._darwin_leader_is_exited
    monitor_failed = False
    hidden_exit_observations = 0
    grace_calls: list[int] = []

    def delayed_waitid(*args: Any) -> Any:
        nonlocal hidden_exit_observations
        if monitor_failed and hidden_exit_observations < 2:
            hidden_exit_observations += 1
            return None
        return original_waitid(*args)

    def exit_then_unavailable(pid: int) -> int:
        nonlocal monitor_failed
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if original_waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None:
                monitor_failed = True
                raise cpu_runner.CpuRunnerError("fixture RSS vanished before exit became visible")
            time.sleep(.001)
        raise AssertionError("fixture exit exceeded bounded watchdog")

    def measured_grace(pid: int) -> bool:
        grace_calls.append(pid)
        return original_grace(pid)

    monkeypatch.setattr(cpu_runner, "_require_supported_host", lambda: None)
    monkeypatch.setattr(transport.sys, "platform", "darwin")
    monkeypatch.setattr(cpu_runner, "_darwin_rss", exit_then_unavailable)
    monkeypatch.setattr(transport.os, "waitid", delayed_waitid)
    monkeypatch.setattr(transport, "_darwin_leader_is_exited", measured_grace)
    result = probe("import time;time.sleep(.05);raise SystemExit(23)")
    assert hidden_exit_observations == 2 and len(grace_calls) == 1
    assert result.observation.trigger == "nonzero-exit" and result.cleanup_error is None
    assert result.primary_error is not None and "unsuccessfully (23)" in str(result.primary_error)
    assert result.observation.returncode_before_cleanup == result.observation.returncode_after_cleanup == 23
    assert result.observation.peak_rss_bytes is result.observation.rss_source is None
    reaped(grace_calls[0])


@pytest.mark.skipif(sys.platform != "linux", reason="Linux subreaper makes orphan cleanup directly waitable")
def test_exited_leader_keeps_group_identity_until_lingering_child_is_stopped() -> None:
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int()
    assert libc.prctl(37, ctypes.byref(previous), 0, 0, 0) == 0
    assert libc.prctl(36, 1, 0, 0, 0) == 0
    child_pid: int | None = None
    try:
        result = probe("import os,time\nchild=os.fork()\n"
                       "if child==0:\n    time.sleep(30)\n"
                       "else:\n    print(str(os.getpid())+' '+str(child),flush=True)\n    os._exit(0)", wall=0.3)
        assert result.stdout is not None
        leader_pid, child_pid = map(int, result.stdout.split())
        observation = result.observation
        assert observation.trigger == "wall-deadline"
        assert observation.state_before_cleanup == "exited" and observation.returncode_before_cleanup == 0
        assert observation.returncode_after_cleanup == 0 and observation.signal_sent and observation.leader_reaped
        reaped(leader_pid)
        deadline = time.monotonic() + 3
        status: int | None = None
        while time.monotonic() < deadline:
            waited, candidate = os.waitpid(child_pid, os.WNOHANG)
            if waited == child_pid:
                status = candidate
                break
            time.sleep(.001)
        assert status is not None and os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL
        reaped(child_pid)
        child_pid = None
    finally:
        if child_pid is not None:
            try:
                os.kill(child_pid, signal.SIGKILL)
                os.waitpid(child_pid, 0)
            except (ProcessLookupError, ChildProcessError):
                pass
        assert libc.prctl(36, previous.value, 0, 0, 0) == 0


@pytest.mark.parametrize("changes", [
    {"schema_version": True}, {"signal_number": True}, {"returncode_after_cleanup": 0},
    {"returncode_before_cleanup": 0}, {"peak_rss_bytes": 12}, {"rss_source": "parent-darwin-proc_pidinfo"},
    {"signal_sent": False}, {"cleanup_state": "completed"}, {"total_seconds": float("inf")},
    {"additional_cleanup_errors": 1}, {"trigger": "completed"}, {"trigger": "stdout-cap"},
    {"cleanup_state": "ownership-lost"},
])
def test_observation_rejects_invented_or_incoherent_facts(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        transport.CpuProcessObservation.model_validate({"trigger": "preflight-failed", "dispatched": False} | changes)


def resource_source(body: str) -> str:
    return f"""
import importlib.util,json,os,sys
spec=importlib.util.spec_from_file_location('fixed_worker',{str(Path(cpu_worker.__file__).resolve())!r})
worker=importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
worker._install_resource_limits(256,1)
print(os.getpid(),flush=True)
{body}
"""


@pytest.mark.skipif(sys.platform != "linux", reason="Linux RLIMIT_AS allocation denial")
def test_linux_allocation_denial_has_observed_exit_and_no_oom_claim() -> None:
    result = probe(resource_source("try:\n    bytearray(512*1024*1024)\nexcept MemoryError:\n    sys.exit(73)"),
                   wall=10.0, memory=256 * 1024 * 1024)
    assert result.observation.trigger == "nonzero-exit"
    assert result.observation.returncode_before_cleanup == result.observation.returncode_after_cleanup == 73
    assert result.observation.peak_rss_bytes is None and result.stdout is not None
    reaped(int(result.stdout))


@pytest.mark.skipif(sys.platform != "linux", reason="Linux RLIMIT_CPU termination")
def test_linux_kernel_signal_is_observed_before_supervisor_cleanup() -> None:
    result = probe(resource_source("while True:\n    pass"), wall=10.0, memory=256 * 1024 * 1024)
    assert result.observation.trigger == "nonzero-exit"
    assert result.observation.returncode_before_cleanup == result.observation.returncode_after_cleanup == -signal.SIGKILL
    assert result.observation.elapsed_to_trigger_seconds is not None and result.observation.elapsed_to_trigger_seconds < 10
    assert result.observation.peak_rss_bytes is None and result.stdout is not None
    reaped(int(result.stdout))
