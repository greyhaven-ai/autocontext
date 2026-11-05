"""Measured private transport for the fixed CPU worker, never arbitrary user code.

Exit status is observed before cleanup with WNOWAIT. A cleanup SIGKILL is an
action by this supervisor, not evidence of an OOM or CPU-limit root cause.
"""

from __future__ import annotations

import math
import os
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from autocontext.execution._process_group import _darwin_leader_is_exited, signal_owned_process_group
from autocontext.weight_evolution import cpu_runner as native
from autocontext.weight_evolution.models import FrozenModel

Trigger = Literal[
    "completed", "nonzero-exit", "wall-deadline", "stdout-cap", "stderr-cap", "rss-limit",
    "rss-monitor-unavailable", "ownership-lost", "launch-failed", "input-rejected", "preflight-failed",
    "transport-error", "cleanup-failed",
]
ProcessState = Literal["not-dispatched", "running", "exited", "ownership-lost"]


class CpuProcessObservation(FrozenModel):
    schema_version: Literal[1] = 1
    trigger: Trigger
    dispatched: bool
    elapsed_to_trigger_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    total_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    cleanup_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    state_before_cleanup: ProcessState = "not-dispatched"
    returncode_before_cleanup: int | None = None
    returncode_after_cleanup: int | None = None
    signal_attempted: bool = False
    signal_sent: bool | None = None
    signal_number: Literal[9] | None = None
    peak_rss_bytes: int | None = Field(default=None, gt=0)
    rss_source: Literal["parent-darwin-proc_pidinfo"] | None = None
    cleanup_state: Literal["not-needed", "completed", "failed", "ownership-lost"] = "not-needed"
    additional_cleanup_errors: int = Field(default=0, ge=0)
    leader_reaped: bool | None = None
    stdout_capped: bool = False
    stderr_capped: bool = False

    @field_validator("schema_version", "signal_number", mode="before")
    @classmethod
    def exact_integer_literals(cls, value: Any) -> Any:
        if value is not None and type(value) is not int:
            raise ValueError("integer literals must be exact integers")
        return value

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if (self.peak_rss_bytes is None) != (self.rss_source is None):
            raise ValueError("RSS value requires its observed provenance")
        if self.signal_attempted != (self.signal_number is not None):
            raise ValueError("signal number requires an attempted action")
        if not self.signal_attempted and self.signal_sent is not None:
            raise ValueError("unattempted signal has no delivery observation")
        if self.returncode_before_cleanup is not None and self.state_before_cleanup != "exited":
            raise ValueError("pre-cleanup returncode requires an observed exited leader")
        if (self.returncode_after_cleanup is not None) != (self.leader_reaped is True):
            raise ValueError("post-cleanup returncode requires a successful reap")
        if self.state_before_cleanup == "ownership-lost" and self.signal_attempted:
            raise ValueError("lost ownership forbids signaling")
        if (self.state_before_cleanup == "ownership-lost") != (self.cleanup_state == "ownership-lost"):
            raise ValueError("lost ownership requires an explicit refused cleanup state")
        if self.stdout_capped != (self.trigger == "stdout-cap") or self.stderr_capped != (self.trigger == "stderr-cap"):
            raise ValueError("stream-cap triggers require the corresponding retained-prefix flag")
        if not self.dispatched:
            if self.trigger not in {"input-rejected", "preflight-failed", "launch-failed", "cleanup-failed"}:
                raise ValueError("nondispatched attempt cannot claim a worker trigger")
            if (self.state_before_cleanup != "not-dispatched" or self.cleanup_state not in {"not-needed", "failed"}
                    or any(value is not None for value in (
                        self.elapsed_to_trigger_seconds, self.returncode_before_cleanup,
                        self.returncode_after_cleanup, self.leader_reaped, self.peak_rss_bytes,
                    )) or self.signal_attempted or self.stdout_capped or self.stderr_capped):
                raise ValueError("nondispatched attempt cannot contain child observations")
            if (self.cleanup_state == "not-needed") != (self.cleanup_seconds is None):
                raise ValueError("failed parent-only cleanup requires its measured duration")
            if self.cleanup_seconds is not None and self.total_seconds is None:
                raise ValueError("measured parent cleanup requires total measured duration")
        elif (self.state_before_cleanup == "not-dispatched" or self.cleanup_state == "not-needed"
              or self.elapsed_to_trigger_seconds is None or self.total_seconds is None or self.cleanup_seconds is None):
            raise ValueError("dispatched attempt requires measured lifecycle observations")
        if self.cleanup_state == "completed" and self.leader_reaped is not True:
            raise ValueError("completed cleanup requires leader reap")
        if self.leader_reaped is False and self.cleanup_state != "failed":
            raise ValueError("unsuccessful reap requires failed cleanup")
        if self.trigger == "completed" and (
            self.returncode_before_cleanup != 0 or self.returncode_after_cleanup != 0 or self.cleanup_state != "completed"
        ):
            raise ValueError("completed transport requires observed zero exit and successful cleanup")
        if self.additional_cleanup_errors and self.cleanup_state not in {"failed", "ownership-lost"}:
            raise ValueError("additional cleanup errors require failed cleanup")
        if self.total_seconds is not None and any(
            value is not None and value > self.total_seconds
            for value in (self.elapsed_to_trigger_seconds, self.cleanup_seconds)
        ):
            raise ValueError("lifecycle interval exceeds total measured duration")
        return self


@dataclass(frozen=True)
class MeasuredTransportResult:
    stdout: bytes | None
    stderr: bytes | None
    observation: CpuProcessObservation
    primary_error: BaseException | None = None
    cleanup_error: BaseException | None = None


def _observe_leader(process: subprocess.Popen[bytes]) -> tuple[ProcessState, int | None]:
    """Keep the waitable child identity pinned until group cleanup is finished."""
    try:
        waitid: Any = getattr(os, "waitid", None)
        info = waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    except (ChildProcessError, OSError, TypeError, ValueError) as exc:
        raise native.CpuRunnerError("measured CPU worker exit ownership was lost") from exc
    if info is None:
        return "running", None
    if info.si_pid != process.pid:
        raise native.CpuRunnerError("measured CPU worker exit identity differs")
    if info.si_code == os.CLD_EXITED:
        return "exited", int(info.si_status)
    if info.si_code in (os.CLD_KILLED, os.CLD_DUMPED):
        return "exited", -int(info.si_status)
    raise native.CpuRunnerError("measured CPU worker returned an unknown wait status")


def _run_measured(command: list[str], payload: bytes, *, wall_seconds: float,
                  memory_bytes: int) -> MeasuredTransportResult:
    """Feed/drain capped pipes once and return facts even when execution fails.

    Only the fixed native caller constructs production commands. Test programs
    exercise this private boundary; it is not a sandbox for untrusted programs.
    Linux address-space/CPU limits are installed by the unchanged fixed worker.
    An exception from a Popen constructor that never returned a handle propagates:
    it may have forked already, so the caller must retain an uncertain claim.
    """
    started = time.monotonic()
    dispatched_at: float | None = None
    triggered_at: float | None = None
    cleanup_started: float | None = None
    cleanup_elapsed: float | None = None
    process: subprocess.Popen[bytes] | None = None
    temporary: tempfile.TemporaryDirectory[str] | None = None
    primary: BaseException | None = None
    launch_error: BaseException | None = None
    cleanup: BaseException | None = None
    cleanup_triggered_at: float | None = None
    additional_cleanup_errors = 0
    trigger: Trigger = "input-rejected"
    before_state: ProcessState = "not-dispatched"
    before_code: int | None = None
    after_code: int | None = None
    attempted = False
    sent: bool | None = None
    peak: int | None = None
    reaped: bool | None = None
    cleanup_state: Literal["not-needed", "completed", "failed", "ownership-lost"] = "not-needed"
    stdout, stderr = bytearray(), bytearray()
    stdout_capped = stderr_capped = False

    def record_cleanup(exc: BaseException) -> None:
        nonlocal cleanup, cleanup_triggered_at, additional_cleanup_errors
        if cleanup is None:
            cleanup = exc
            cleanup_triggered_at = time.monotonic()
        else:
            # Keep the first actual error intact; do not invent cause/context links.
            additional_cleanup_errors += 1

    try:
        if (type(payload) is not bytes or len(payload) > native.MAX_REQUEST_BYTES
                or type(wall_seconds) not in {int, float} or not math.isfinite(wall_seconds)
                or not 0.1 <= wall_seconds <= 30.0 or type(memory_bytes) is not int
                or not 256 * 1024 * 1024 <= memory_bytes <= 2048 * 1024 * 1024
                or type(command) is not list or not command or len(command) > 32
                or any(type(part) is not str or not part or len(part) > native.MAX_REQUEST_BYTES for part in command)):
            raise native.CpuRunnerError("measured CPU transport inputs exceed the fixed worker bounds")
        trigger = "preflight-failed"
        native._require_supported_host()
        if not all(hasattr(os, name) for name in ("waitid", "P_PID", "WEXITED", "WNOHANG", "WNOWAIT")):
            raise native.CpuRunnerError("measured CPU transport requires non-reaping child observation")
        temporary = tempfile.TemporaryDirectory(prefix="autocontext-measured-cpu-")
        environment = {"PATH": os.defpath, "HOME": temporary.name, "TMPDIR": temporary.name,
                       "LANG": "C.UTF-8", "CUDA_VISIBLE_DEVICES": "", "NVIDIA_VISIBLE_DEVICES": "void",
                       "HIP_VISIBLE_DEVICES": "", "ROCR_VISIBLE_DEVICES": "", "PYTORCH_ENABLE_MPS_FALLBACK": "0"}
        trigger = "launch-failed"
        launch_started = time.monotonic()
        try:
            process = subprocess.Popen(  # noqa: S603
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=temporary.name, env=environment, start_new_session=True, close_fds=True,
            )
        except BaseException as exc:
            # Even an OSError may follow fork. Without a returned owned handle,
            # no safe status, dispatch, or cleanup claim can be reconstructed.
            launch_error = exc
            raise
        dispatched_at = launch_started
        trigger = "transport-error"
        assert process.stdin is not None and process.stdout is not None and process.stderr is not None
        with selectors.DefaultSelector() as selector:
            for stream, event, label in ((process.stdin, selectors.EVENT_WRITE, "stdin"),
                                          (process.stdout, selectors.EVENT_READ, "stdout"),
                                          (process.stderr, selectors.EVENT_READ, "stderr")):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, event, label)
            offset = 0
            next_rss = time.monotonic() + native.RSS_POLL_SECONDS
            while True:
                try:
                    state, observed_code = _observe_leader(process)
                except native.CpuRunnerError:
                    trigger = "ownership-lost"
                    raise
                if not selector.get_map() and state == "exited":
                    if observed_code != 0:
                        trigger = "nonzero-exit"
                        raise native.CpuRunnerError(f"measured CPU worker exited unsuccessfully ({observed_code})")
                    break
                remaining = wall_seconds - (time.monotonic() - dispatched_at)
                if remaining <= 0:
                    trigger = "wall-deadline"
                    raise native.CpuRunnerError("measured CPU worker exceeded wall deadline")
                if sys.platform == "darwin" and state == "running" and time.monotonic() >= next_rss:
                    try:
                        observed = native._darwin_rss(process.pid)
                        if observed <= 0:
                            raise native.CpuRunnerError("Darwin CPU worker RSS monitoring returned no positive measurement")
                        peak = max(peak or 0, observed)
                    except native.CpuRunnerError:
                        try:
                            state, _ = _observe_leader(process)
                            if state == "running" and _darwin_leader_is_exited(process.pid):
                                # proc_pidinfo can lose an exiting process just
                                # before waitid exposes it. Preserve the native
                                # bounded grace, then obtain our own exit facts.
                                state, _ = _observe_leader(process)
                        except native.CpuRunnerError:
                            trigger = "ownership-lost"
                            raise
                        if state != "exited":
                            trigger = "rss-monitor-unavailable"
                            raise
                    if peak is not None and peak > memory_bytes:
                        trigger = "rss-limit"
                        raise native.CpuRunnerError("measured CPU worker exceeded monitored RSS memory limit")
                    next_rss = time.monotonic() + native.RSS_POLL_SECONDS
                for key, _ in selector.select(min(remaining, native.RSS_POLL_SECONDS)):
                    if key.data == "stdin":
                        try:
                            offset += os.write(key.fd, payload[offset:offset + 16384])
                        except BrokenPipeError:
                            offset = len(payload)
                        if offset == len(payload):
                            selector.unregister(key.fileobj)
                            process.stdin.close()
                        continue
                    chunk = os.read(key.fd, 16384)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    output, limit = ((stdout, native.MAX_STDOUT_BYTES) if key.data == "stdout"
                                     else (stderr, native.MAX_STDERR_BYTES))
                    available = limit - len(output)
                    output.extend(chunk[:available])
                    if len(chunk) > available:
                        if key.data == "stdout":
                            trigger, stdout_capped = "stdout-cap", True
                        else:
                            trigger, stderr_capped = "stderr-cap", True
                        raise native.CpuRunnerError(f"measured CPU worker {key.data} exceeds byte cap")
        trigger = "completed"
    except BaseException as exc:
        primary = exc
    finally:
        triggered_at = time.monotonic()
        if process is not None:
            cleanup_started = time.monotonic()
            cleanup_state = "failed"
            try:
                # Never poll()/wait() before this snapshot or before group signaling.
                if trigger == "ownership-lost":
                    raise native.CpuRunnerError("cleanup refused after CPU worker ownership was lost") from primary
                before_state, before_code = _observe_leader(process)
            except BaseException as exc:
                before_state = "ownership-lost"
                cleanup_state = "ownership-lost"
                cleanup = exc
                if primary is None:
                    primary, trigger = exc, "ownership-lost"
            else:
                if primary is None and before_code not in (None, 0):
                    trigger = "nonzero-exit"
                    primary = native.CpuRunnerError(f"measured CPU worker exited unsuccessfully ({before_code})")
                try:
                    attempted = True
                    sent = signal_owned_process_group(process.pid, signal.SIGKILL)
                except BaseException as exc:
                    record_cleanup(exc)
                try:
                    after_code = process.wait(timeout=5)
                    reaped = True
                except BaseException as exc:
                    record_cleanup(exc)
                    reaped = False
                if cleanup is None:
                    cleanup_state = "completed"
            for final_stream in (process.stdin, process.stdout, process.stderr):
                if final_stream is not None:
                    try:
                        final_stream.close()
                    except BaseException as exc:
                        record_cleanup(exc)
                        if cleanup_state != "ownership-lost":
                            cleanup_state = "failed"
        if temporary is not None:
            if cleanup_started is None:
                cleanup_started = time.monotonic()
            try:
                temporary.cleanup()
            except BaseException as exc:
                record_cleanup(exc)
                if cleanup_state != "ownership-lost":
                    cleanup_state = "failed"
        if cleanup_started is not None and (process is not None or cleanup is not None):
            cleanup_elapsed = time.monotonic() - cleanup_started
        if primary is None and cleanup is not None:
            trigger = "cleanup-failed"
            triggered_at = cleanup_triggered_at or time.monotonic()
    if launch_error is not None:
        raise launch_error
    observation = CpuProcessObservation(
        trigger=trigger, dispatched=process is not None,
        elapsed_to_trigger_seconds=None if dispatched_at is None else triggered_at - dispatched_at,
        total_seconds=time.monotonic() - started, cleanup_seconds=cleanup_elapsed,
        state_before_cleanup=before_state, returncode_before_cleanup=before_code, returncode_after_cleanup=after_code,
        signal_attempted=attempted, signal_sent=sent, signal_number=9 if attempted else None,
        peak_rss_bytes=peak, rss_source="parent-darwin-proc_pidinfo" if peak is not None else None,
        cleanup_state=cleanup_state, leader_reaped=reaped, stdout_capped=stdout_capped, stderr_capped=stderr_capped,
        additional_cleanup_errors=additional_cleanup_errors,
    )
    return MeasuredTransportResult(None if process is None else bytes(stdout), None if process is None else bytes(stderr),
                                   observation, primary, cleanup)
