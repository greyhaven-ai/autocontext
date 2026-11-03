"""Private stdlib transport entry point for the trusted CPU inference worker."""

from __future__ import annotations

import json
import math
import os
import sys
import time
from pathlib import Path

MAX_PROC_STATUS_BYTES = 64 * 1024


def _linux_peak_rss(status_path: Path = Path("/proc/self/status")) -> int:
    """Read the current exec image's peak, excluding inherited pre-exec RSS.

    Linux getrusage preserves the previous address space's high-water mark
    across exec; VmHWM belongs to the new mm instead. The RLIMIT_AS check and
    parent receipt validation remain authoritative for the requested limit.
    """
    with status_path.open("rb") as stream:
        raw = stream.read(MAX_PROC_STATUS_BYTES + 1)
    if len(raw) > MAX_PROC_STATUS_BYTES:
        raise ValueError("Linux worker memory status exceeds its byte cap")
    rows = [line.split() for line in raw.splitlines() if line.startswith(b"VmHWM:")]
    if (len(rows) != 1 or len(rows[0]) != 3 or rows[0][2] != b"kB"
            or not rows[0][1].isdigit() or len(rows[0][1]) > 20):
        raise ValueError("Linux worker memory status lacks one valid VmHWM in kB")
    peak = int(rows[0][1]) * 1024
    if peak <= 0:
        raise ValueError("Linux worker peak RSS must be positive")
    return peak


def _audit_guard(event: str, arguments: tuple[object, ...]) -> None:
    """Catch accidental external execution in trusted code, not hostile code."""
    if event.startswith("socket.") or event in {"subprocess.Popen", "os.system", "os.fork", "os.posix_spawn", "os.exec"}:
        raise RuntimeError("CPU worker forbids network and child-process operations")
    if event == "import" and arguments and str(arguments[0]).split(".", 1)[0] in {
        "torch", "mlx", "mlx_lm", "numpy", "transformers", "tensorflow", "cupy", "jax",
    }:
        raise RuntimeError("CPU worker permits only the trusted stdlib inference implementation")


def _install_resource_limits(memory_mb: int, cpu_seconds: int) -> int:
    """Install and verify the real worker limits before loading model code.

    Kept stdlib-only so Linux acceptance probes exercise the same installation
    path as inference, without adding failure-injection modes to the worker.
    """
    if os.name != "posix" or sys.platform not in {"linux", "darwin"}:
        raise ValueError("CPU worker requires supported POSIX resource limits")
    import resource

    if (type(memory_mb) is not int or type(cpu_seconds) is not int
            or not 256 <= memory_mb <= 2048 or not 1 <= cpu_seconds <= 30):
        raise ValueError("CPU worker limits are outside the supported bounds")
    memory_bytes = memory_mb * 1024 * 1024
    if sys.platform == "linux":
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if ((sys.platform == "linux" and resource.getrlimit(resource.RLIMIT_AS) != (memory_bytes, memory_bytes))
            or resource.getrlimit(resource.RLIMIT_CPU) != (cpu_seconds, cpu_seconds)
            or resource.getrlimit(resource.RLIMIT_CORE) != (0, 0)):
        raise ValueError("CPU worker resource limits were not installed")
    return memory_bytes


def main() -> None:
    if len(sys.argv) != 3:
        raise ValueError("CPU worker requires memory and CPU limits")
    memory_mb, cpu_seconds = (int(value) for value in sys.argv[1:])
    memory_bytes = _install_resource_limits(memory_mb, cpu_seconds)
    import resource

    sys.addaudithook(_audit_guard)
    # -I removes ambient Python paths; add only this trusted checkout's source root.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from autocontext.weight_evolution.artifacts import _unique_object, canonical_digest
    from autocontext.weight_evolution.cpu_model import infer_cpu
    from autocontext.weight_evolution.cpu_models import CpuInferenceRequest
    from autocontext.weight_evolution.cpu_runner import (
        MAX_BATCH_REQUESTS,
        MAX_REQUEST_BYTES,
        MAX_STDOUT_BYTES,
        CpuBatchReceipt,
        CpuTelemetry,
        _batch_digest,
        resource_policy,
        runtime_identity,
    )

    started = time.monotonic()
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError("CPU request exceeds byte cap")
    envelope = json.loads(raw, object_pairs_hook=_unique_object)
    if (not isinstance(envelope, dict)
            or set(envelope) != {"schema_version", "requests", "checkpoint_path", "runtime_digest"}
            or type(envelope["schema_version"]) is not int or envelope["schema_version"] != 1
            or not isinstance(envelope["requests"], list) or not 1 <= len(envelope["requests"]) <= MAX_BATCH_REQUESTS
            or not isinstance(envelope["checkpoint_path"], str)):
        raise ValueError("invalid CPU request envelope")
    requests = tuple(CpuInferenceRequest.model_validate_json(json.dumps(value, allow_nan=False))
                     for value in envelope["requests"])
    first = requests[0]
    if (len({request.request_id for request in requests}) != len(requests)
            or any((request.model, request.checkpoint_sha256, request.checkpoint_size_bytes, request.limits)
                   != (first.model, first.checkpoint_sha256, first.checkpoint_size_bytes, first.limits)
                   for request in requests)
            or first.limits.memory_mb != memory_mb or max(1, math.ceil(first.limits.wall_seconds)) != cpu_seconds):
        raise ValueError("CPU request limits or checkpoint/model identities differ")
    runtime_digest = canonical_digest(runtime_identity())
    if envelope["runtime_digest"] != runtime_digest:
        raise ValueError("CPU worker runtime differs from the dispatch pin")
    results = tuple(infer_cpu(request, Path(envelope["checkpoint_path"])) for request in requests)
    peak_rss = (_linux_peak_rss() if sys.platform == "linux"
                else int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss))
    policy = resource_policy()
    receipt = CpuBatchReceipt(
        batch_digest=_batch_digest(requests, runtime_digest), request_digests=tuple(request.digest for request in requests),
        checkpoint_sha256=first.checkpoint_sha256, checkpoint_size_bytes=first.checkpoint_size_bytes,
        model_digest=first.model.digest, runtime_digest=runtime_digest, results=results,
        telemetry=CpuTelemetry(elapsed_seconds=time.monotonic() - started, peak_rss_bytes=peak_rss,
                               memory_enforcement=policy["memory_enforcement"], memory_limit_bytes=memory_bytes,
                               address_space_limit_bytes=memory_bytes if sys.platform == "linux" else None,
                               rss_poll_interval_seconds=policy["rss_poll_interval_seconds"],
                               rss_sampling_may_overshoot=policy["rss_sampling_may_overshoot"],
                               cpu_limit_seconds=cpu_seconds),
    )
    encoded = json.dumps(receipt.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(encoded) > MAX_STDOUT_BYTES:
        raise ValueError("CPU response exceeds byte cap")
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        sys.stderr.write(f"CPU worker failed: {type(exc).__name__}: {str(exc)[:160]}\n")
        sys.exit(1)
