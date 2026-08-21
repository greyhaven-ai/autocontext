"""Fail-closed GPU resource telemetry policy for kernel reports."""

from __future__ import annotations

from dataclasses import dataclass

from autocontext.kernel_evolution.models import KernelBenchmarkReport


@dataclass(frozen=True, slots=True)
class KernelResourcePolicyResult:
    reason: str | None = None
    detail: str = ""


def evaluate_kernel_resource_policy(
    report: KernelBenchmarkReport,
    *,
    require_telemetry: bool,
    max_gpu_memory_bytes: int | None,
) -> KernelResourcePolicyResult:
    """Validate complete, identity-bound, independently measured CUDA peaks."""

    resources = report.resources
    required = {
        "candidate_artifact_digest": resources.candidate_artifact_digest,
        "incumbent_artifact_digest": resources.incumbent_artifact_digest,
        "candidate_peak_allocated_bytes": resources.candidate_peak_allocated_bytes,
        "candidate_peak_reserved_bytes": resources.candidate_peak_reserved_bytes,
        "incumbent_peak_allocated_bytes": resources.incumbent_peak_allocated_bytes,
        "incumbent_peak_reserved_bytes": resources.incumbent_peak_reserved_bytes,
        "device_total_memory_bytes": resources.device_total_memory_bytes,
    }
    if require_telemetry or max_gpu_memory_bytes is not None:
        missing = sorted(name for name, value in required.items() if value is None)
        if missing:
            return KernelResourcePolicyResult(
                "missing_resource_telemetry",
                f"Required CUDA resource telemetry is missing: {', '.join(missing)}.",
            )
    if resources.candidate_artifact_digest not in {None, report.candidate_artifact_digest}:
        return KernelResourcePolicyResult("resource_identity_mismatch", "Candidate CUDA telemetry identity does not match.")
    if resources.incumbent_artifact_digest not in {None, report.incumbent_artifact_digest}:
        return KernelResourcePolicyResult("resource_identity_mismatch", "Incumbent CUDA telemetry identity does not match.")
    if max_gpu_memory_bytes is not None:
        candidate_peak = resources.candidate_enforced_peak_bytes
        incumbent_values = (
            resources.incumbent_peak_allocated_bytes,
            resources.incumbent_peak_reserved_bytes,
            resources.incumbent_peak_memory_bytes,
        )
        incumbent_present = [value for value in incumbent_values if value is not None]
        incumbent_peak = max(incumbent_present) if incumbent_present else None
        if candidate_peak is not None and candidate_peak > max_gpu_memory_bytes:
            return KernelResourcePolicyResult(
                "resource_exceeded",
                f"Candidate CUDA peak {candidate_peak} exceeds enforced limit {max_gpu_memory_bytes} bytes.",
            )
        if incumbent_peak is not None and incumbent_peak > max_gpu_memory_bytes:
            return KernelResourcePolicyResult(
                "resource_exceeded",
                f"Incumbent CUDA peak {incumbent_peak} exceeds enforced limit {max_gpu_memory_bytes} bytes.",
            )
    return KernelResourcePolicyResult()


__all__ = ["KernelResourcePolicyResult", "evaluate_kernel_resource_policy"]
