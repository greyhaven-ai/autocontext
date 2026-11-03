"""Bounded execution of the trusted tiny CPU model in an isolated subprocess.

This is a resource boundary for a fixed implementation, not a sandbox for user
code. Linux enforces RLIMIT_AS; Darwin monitors RSS with possible sampling
overshoot. Unsupported hosts and unavailable monitoring fail before dispatch.
"""

from __future__ import annotations

import json
import math
import os
import platform
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from autocontext.weight_evolution.artifacts import _unique_object, canonical_digest, file_identity
from autocontext.weight_evolution.cpu_model import decode_cpu_logits
from autocontext.weight_evolution.cpu_models import CpuInferenceRequest, CpuInferenceResult
from autocontext.weight_evolution.models import Digest, FrozenModel

MAX_BATCH_REQUESTS = 32
MAX_REQUEST_BYTES = 512 * 1024
MAX_STDOUT_BYTES = 128 * 1024
MAX_STDERR_BYTES = 16 * 1024
RSS_POLL_SECONDS = 0.01


class CpuRunnerError(ValueError):
    """A worker failed its resource, transport, or evidence contract."""


class CpuTelemetry(FrozenModel):
    elapsed_seconds: float = Field(gt=0, allow_inf_nan=False)
    peak_rss_bytes: int = Field(gt=0)
    memory_enforcement: Literal["hard_address_space", "monitored_rss"]
    memory_limit_bytes: int = Field(gt=0)
    address_space_limit_bytes: int | None = Field(default=None, gt=0)
    rss_poll_interval_seconds: float = Field(ge=0, allow_inf_nan=False)
    rss_sampling_may_overshoot: bool
    cpu_limit_seconds: int = Field(gt=0)
    returncode: Literal[0] = 0


class CpuBatchReceipt(FrozenModel):
    schema_version: Literal[1] = 1
    execution_kind: Literal["cpu_subprocess"] = "cpu_subprocess"
    model_execution_performed: Literal[True] = True
    device: Literal["cpu"] = "cpu"
    gpu_execution_performed: Literal[False] = False
    batch_digest: Digest
    request_digests: tuple[Digest, ...] = Field(min_length=1, max_length=MAX_BATCH_REQUESTS)
    checkpoint_sha256: Digest
    checkpoint_size_bytes: int = Field(gt=0)
    model_digest: Digest
    runtime_digest: Digest
    results: tuple[CpuInferenceResult, ...] = Field(min_length=1, max_length=MAX_BATCH_REQUESTS)
    telemetry: CpuTelemetry

    @property
    def semantic_digest(self) -> str:
        value = self.to_dict()
        value.pop("telemetry")
        return canonical_digest(value)

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())


def runtime_identity() -> dict[str, Any]:
    """Pin interpreter, platform, and the trusted model/transport source closure."""
    import pydantic
    import pydantic_core

    package = Path(__file__).parent
    names = ("cpu_models.py", "cpu_model.py", "cpu_runner.py", "cpu_worker.py", "artifacts.py", "models.py", "tensor_edits.py")
    sources = [file_identity(package / name, max_bytes=1024 * 1024) for name in names]
    sources.append(file_identity(package.parent / "util" / "models.py", max_bytes=1024 * 1024))
    sources.append(file_identity(package.parent / "util" / "file_lock.py", max_bytes=1024 * 1024))
    sources.append(file_identity(package.parent / "execution" / "_process_group.py", max_bytes=1024 * 1024))
    return {"schema_version": 1, "implementation": "trusted-token-sum-cpu-subprocess-v1",
            "python": platform.python_version(), "python_implementation": platform.python_implementation(),
            "system": platform.system(), "release": platform.release(), "machine": platform.machine(),
            "executable": file_identity(Path(sys.executable).resolve(), max_bytes=128 * 1024 * 1024),
            "pydantic": pydantic.__version__, "pydantic_core": pydantic_core.__version__, "sources": sources,
            "device": "cpu", "gpu_execution_performed": False,
            "resource_policy": resource_policy()}


def resource_policy() -> dict[str, Any]:
    return {"memory_enforcement": "hard_address_space" if sys.platform == "linux" else "monitored_rss",
            "peak_rss_source": "proc-self-status-VmHWM" if sys.platform == "linux" else "getrusage-self-bytes",
            "rss_poll_interval_seconds": 0.0 if sys.platform == "linux" else RSS_POLL_SECONDS,
            "rss_sampling_may_overshoot": sys.platform != "linux", "cpu_limit": "rlimit_cpu",
            "wall_limit": "parent_monotonic", "scope": "trusted_fixed_model_only"}


def _darwin_rss(pid: int) -> int:
    """Read PROC_PIDTASKINFO resident bytes using the public macOS libproc ABI."""
    import ctypes

    class TaskInfo(ctypes.Structure):
        _fields_ = [("virtual_size", ctypes.c_uint64), ("resident_size", ctypes.c_uint64),
                    ("times", ctypes.c_uint64 * 4), ("counters", ctypes.c_int32 * 12)]

    library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    inspect = library.proc_pidinfo
    inspect.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
    inspect.restype = ctypes.c_int
    info = TaskInfo()
    if inspect(pid, 4, 0, ctypes.byref(info), ctypes.sizeof(info)) != ctypes.sizeof(info):
        raise CpuRunnerError("Darwin CPU worker RSS monitoring is unavailable")
    return int(info.resident_size)


def _require_supported_host() -> None:
    if os.name != "posix" or sys.platform not in {"linux", "darwin"}:
        raise CpuRunnerError("bounded CPU subprocess inference requires supported Linux or Darwin POSIX limits")
    if sys.platform == "darwin" and _darwin_rss(os.getpid()) <= 0:
        raise CpuRunnerError("Darwin RSS monitoring failed its pre-dispatch check")


def _kill_and_reap(process: subprocess.Popen[bytes]) -> None:
    from autocontext.execution._process_group import signal_owned_process_group

    # Kill the whole owned group even if its leader has exited with open pipes.
    signal_owned_process_group(process.pid, signal.SIGKILL)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired as exc:
        raise CpuRunnerError("CPU worker did not stop during bounded cleanup") from exc


def _leader_running(process: subprocess.Popen[bytes]) -> bool:
    from autocontext.execution._process_group import process_state_without_reaping

    state = process_state_without_reaping(process, lambda *args, **kwargs: [])
    if state == "ownership_lost":
        raise CpuRunnerError("CPU worker exit ownership was lost")
    return state == "running"


def _run_bounded(command: list[str], payload: bytes, *, wall_seconds: float,
                 memory_bytes: int | None = None) -> tuple[bytes, bytes, float, int, int]:
    """Simultaneously drain capped pipes and feed bounded stdin before deadline."""
    if len(payload) > MAX_REQUEST_BYTES:
        raise CpuRunnerError("CPU batch request exceeds byte cap")
    started = time.monotonic()
    peak_rss = 0
    stdout, stderr = bytearray(), bytearray()
    with tempfile.TemporaryDirectory(prefix="autocontext-cpu-worker-") as temporary:
        environment = {"PATH": os.defpath, "HOME": temporary, "TMPDIR": temporary,
                       "LANG": "C.UTF-8", "CUDA_VISIBLE_DEVICES": "", "NVIDIA_VISIBLE_DEVICES": "void",
                       "HIP_VISIBLE_DEVICES": "", "ROCR_VISIBLE_DEVICES": "", "PYTORCH_ENABLE_MPS_FALLBACK": "0"}
        process = subprocess.Popen(  # noqa: S603
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=temporary, env=environment, start_new_session=True, close_fds=True,
        )
        assert process.stdin is not None and process.stdout is not None and process.stderr is not None
        try:
            with selectors.DefaultSelector() as selector:
                for stream, event, label in ((process.stdin, selectors.EVENT_WRITE, "stdin"),
                                              (process.stdout, selectors.EVENT_READ, "stdout"),
                                              (process.stderr, selectors.EVENT_READ, "stderr")):
                    os.set_blocking(stream.fileno(), False)
                    selector.register(stream, event, label)
                offset = 0
                next_rss_check = time.monotonic() + RSS_POLL_SECONDS
                while selector.get_map() or _leader_running(process):
                    remaining = wall_seconds - (time.monotonic() - started)
                    if remaining <= 0:
                        raise CpuRunnerError("CPU worker exceeded wall deadline")
                    if (memory_bytes is not None and sys.platform == "darwin" and _leader_running(process)
                            and time.monotonic() >= next_rss_check):
                        try:
                            peak_rss = max(peak_rss, _darwin_rss(process.pid))
                        except CpuRunnerError:
                            from autocontext.execution._process_group import _darwin_leader_is_exited

                            if not _darwin_leader_is_exited(process.pid):
                                raise
                        if peak_rss > memory_bytes:
                            raise CpuRunnerError("CPU worker exceeded monitored RSS memory limit")
                        next_rss_check = time.monotonic() + RSS_POLL_SECONDS
                    for key, _ in selector.select(min(remaining, RSS_POLL_SECONDS)):
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
                        output, limit = ((stdout, MAX_STDOUT_BYTES) if key.data == "stdout"
                                         else (stderr, MAX_STDERR_BYTES))
                        if len(output) + len(chunk) > limit:
                            raise CpuRunnerError(f"CPU worker {key.data} exceeds byte cap")
                        output.extend(chunk)
                _kill_and_reap(process)
                code = process.returncode
                assert code is not None
                return bytes(stdout), bytes(stderr), time.monotonic() - started, code, peak_rss
        finally:
            try:
                if process.returncode is None:
                    _kill_and_reap(process)
            finally:
                for stream in (process.stdin, process.stdout, process.stderr):
                    stream.close()


def _batch_digest(requests: tuple[CpuInferenceRequest, ...], runtime_digest: str) -> str:
    return canonical_digest({"request_digests": [request.digest for request in requests], "runtime_digest": runtime_digest})


def _validate_response(raw: bytes, requests: tuple[CpuInferenceRequest, ...], *, runtime_digest: str,
                       elapsed: float, returncode: int, parent_peak_rss: int = 0) -> CpuBatchReceipt:
    if returncode != 0:
        raise CpuRunnerError(f"CPU worker exited unsuccessfully ({returncode})")
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
        receipt = CpuBatchReceipt.model_validate_json(json.dumps(value, allow_nan=False))
        if canonical_digest(value) != canonical_digest(receipt.to_dict()):
            raise ValueError("response contains normalized or omitted fields")
    except (ValueError, TypeError, UnicodeError) as exc:
        raise CpuRunnerError("CPU worker returned an invalid response envelope") from exc
    first = requests[0]
    if not math.isfinite(elapsed) or not 0 < elapsed <= first.limits.wall_seconds:
        raise CpuRunnerError("CPU worker elapsed time exceeds its wall deadline")
    expected = {"batch_digest": _batch_digest(requests, runtime_digest),
                "request_digests": tuple(request.digest for request in requests),
                "checkpoint_sha256": first.checkpoint_sha256, "checkpoint_size_bytes": first.checkpoint_size_bytes,
                "model_digest": first.model.digest, "runtime_digest": runtime_digest}
    if any(getattr(receipt, name) != expected_value for name, expected_value in expected.items()):
        raise CpuRunnerError("CPU worker response differs from batch/request/checkpoint/runtime bindings")
    if len(receipt.results) != len(requests):
        raise CpuRunnerError("CPU worker returned the wrong result count")
    for request, result in zip(requests, receipt.results, strict=True):
        if (result.request_digest != request.digest or result.checkpoint_sha256 != request.checkpoint_sha256
                or result.checkpoint_size_bytes != request.checkpoint_size_bytes
                or result.input_tokens != tuple(request.model.vocabulary.index(token) for token in request.prompt.split())
                or any(token < 0 or token >= len(request.model.vocabulary) for token in result.input_tokens)
                or len(result.logits) != len(request.model.output_tokens)
                or not 0 <= result.output_token_id < len(request.model.output_tokens)
                or result.output_token_id != decode_cpu_logits(result.logits, request.decoding)
                or result.text != request.model.output_tokens[result.output_token_id]):
            raise CpuRunnerError("CPU worker result differs from its request or model contract")
    memory = first.limits.memory_mb * 1024 * 1024
    cpu_seconds = max(1, math.ceil(first.limits.wall_seconds))
    policy = resource_policy()
    if (receipt.telemetry.address_space_limit_bytes != (memory if sys.platform == "linux" else None)
            or receipt.telemetry.memory_limit_bytes != memory or receipt.telemetry.cpu_limit_seconds != cpu_seconds
            or receipt.telemetry.memory_enforcement != policy["memory_enforcement"]
            or receipt.telemetry.rss_poll_interval_seconds != policy["rss_poll_interval_seconds"]
            or receipt.telemetry.rss_sampling_may_overshoot != policy["rss_sampling_may_overshoot"]
            or max(receipt.telemetry.peak_rss_bytes, parent_peak_rss) > memory):
        raise CpuRunnerError("CPU worker resource-limit evidence differs from the requested bounds")
    # Wall duration is observed by the supervising parent, never trusted from stdout.
    return receipt.model_copy(update={"telemetry": receipt.telemetry.model_copy(update={
        "elapsed_seconds": elapsed, "peak_rss_bytes": max(receipt.telemetry.peak_rss_bytes, parent_peak_rss),
    })})


def validate_cpu_receipt(receipt: CpuBatchReceipt, requests: tuple[CpuInferenceRequest, ...],
                         runtime_digest: str) -> CpuBatchReceipt:
    """Verify retained response/request/resource bindings without model execution."""
    if not isinstance(requests, tuple) or not 1 <= len(requests) <= MAX_BATCH_REQUESTS:
        raise CpuRunnerError("CPU receipt requires 1..32 requests")
    requests = tuple(CpuInferenceRequest.model_validate_json(json.dumps(request.to_dict(), allow_nan=False))
                     for request in requests)
    first = requests[0]
    if (len({request.request_id for request in requests}) != len(requests)
            or any((request.model, request.checkpoint_sha256, request.checkpoint_size_bytes, request.limits)
                   != (first.model, first.checkpoint_sha256, first.checkpoint_size_bytes, first.limits)
                   for request in requests)):
        raise CpuRunnerError("CPU receipt requests must share model, checkpoint and limits with unique IDs")
    raw = json.dumps(receipt.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(raw) > MAX_STDOUT_BYTES:
        raise CpuRunnerError("CPU receipt exceeds response byte cap")
    return _validate_response(raw, requests, runtime_digest=runtime_digest, elapsed=receipt.telemetry.elapsed_seconds,
                              returncode=receipt.telemetry.returncode)


def run_cpu_batch(requests: tuple[CpuInferenceRequest, ...], *, checkpoint_path: Path) -> CpuBatchReceipt:
    """Execute up to 32 requests sharing one model/checkpoint/resource contract."""
    if not isinstance(requests, tuple) or not 1 <= len(requests) <= MAX_BATCH_REQUESTS:
        raise CpuRunnerError("CPU batch requires 1..32 requests")
    requests = tuple(CpuInferenceRequest.model_validate_json(json.dumps(request.to_dict(), allow_nan=False))
                     for request in requests)
    first = requests[0]
    if len({request.request_id for request in requests}) != len(requests):
        raise CpuRunnerError("CPU batch repeats a request ID")
    if any((request.model, request.checkpoint_sha256, request.checkpoint_size_bytes, request.limits)
           != (first.model, first.checkpoint_sha256, first.checkpoint_size_bytes, first.limits) for request in requests):
        raise CpuRunnerError("CPU batch must share model, checkpoint and limits")
    _require_supported_host()
    identity = file_identity(checkpoint_path, max_bytes=first.checkpoint_size_bytes)
    if identity["sha256"] != first.checkpoint_sha256 or identity["size_bytes"] != first.checkpoint_size_bytes:
        raise CpuRunnerError("CPU batch checkpoint differs from its pin")
    runtime_digest = canonical_digest(runtime_identity())
    envelope = {"schema_version": 1, "requests": [request.to_dict() for request in requests],
                "checkpoint_path": str(checkpoint_path.absolute()), "runtime_digest": runtime_digest}
    payload = json.dumps(envelope, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    command = [sys.executable, "-I", str(Path(__file__).with_name("cpu_worker.py")),
               str(first.limits.memory_mb), str(max(1, math.ceil(first.limits.wall_seconds)))]
    raw, error, elapsed, code, peak_rss = _run_bounded(command, payload, wall_seconds=first.limits.wall_seconds,
                                                    memory_bytes=first.limits.memory_mb * 1024 * 1024)
    if error:
        raise CpuRunnerError(f"CPU worker reported stderr: {error[:240].decode('utf-8', errors='replace')}")
    receipt = _validate_response(raw, requests, runtime_digest=runtime_digest, elapsed=elapsed, returncode=code,
                                 parent_peak_rss=peak_rss)
    if canonical_digest(runtime_identity()) != runtime_digest:
        raise CpuRunnerError("CPU runtime sources changed during inference")
    if file_identity(checkpoint_path, max_bytes=first.checkpoint_size_bytes) != identity:
        raise CpuRunnerError("CPU checkpoint changed during inference")
    return receipt
