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
    """Validate complete, identity-bound, independently measured accelerator peaks."""

    resources = report.resources
    identity_required = {
        "candidate_artifact_digest": resources.candidate_artifact_digest,
        "incumbent_artifact_digest": resources.incumbent_artifact_digest,
        "device_total_memory_bytes": resources.device_total_memory_bytes,
    }
    if require_telemetry or max_gpu_memory_bytes is not None:
        missing = sorted(name for name, value in identity_required.items() if value is None)
        observed_complete = (
            resources.telemetry_authority == "trusted-evaluator-observed/v1"
            and resources.accelerator_attestation_digest is not None
            and resources.candidate_observed_peak_bytes is not None
            and resources.incumbent_observed_peak_bytes is not None
        )
        allocator_complete = all(
            value is not None
            for value in (
                resources.candidate_peak_allocated_bytes,
                resources.candidate_peak_reserved_bytes,
                resources.incumbent_peak_allocated_bytes,
                resources.incumbent_peak_reserved_bytes,
            )
        )
        if not observed_complete and not allocator_complete:
            missing.append("complete allocator or trusted-evaluator peak telemetry")
        if missing:
            return KernelResourcePolicyResult(
                "missing_resource_telemetry",
                f"Required accelerator resource telemetry is missing: {', '.join(missing)}.",
            )
    if resources.candidate_artifact_digest not in {None, report.candidate_artifact_digest}:
        return KernelResourcePolicyResult(
            "resource_identity_mismatch", "Candidate accelerator telemetry identity does not match."
        )
    if resources.incumbent_artifact_digest not in {None, report.incumbent_artifact_digest}:
        return KernelResourcePolicyResult(
            "resource_identity_mismatch", "Incumbent accelerator telemetry identity does not match."
        )
    if max_gpu_memory_bytes is not None:
        candidate_peak = resources.candidate_enforced_peak_bytes
        incumbent_values = (
            resources.incumbent_peak_allocated_bytes,
            resources.incumbent_peak_reserved_bytes,
            resources.incumbent_peak_memory_bytes,
            resources.incumbent_observed_peak_bytes,
        )
        incumbent_present = [value for value in incumbent_values if value is not None]
        incumbent_peak = max(incumbent_present) if incumbent_present else None
        if candidate_peak is not None and candidate_peak > max_gpu_memory_bytes:
            return KernelResourcePolicyResult(
                "resource_exceeded",
                f"Candidate accelerator peak {candidate_peak} exceeds enforced limit {max_gpu_memory_bytes} bytes.",
            )
        if incumbent_peak is not None and incumbent_peak > max_gpu_memory_bytes:
            return KernelResourcePolicyResult(
                "resource_exceeded",
                f"Incumbent accelerator peak {incumbent_peak} exceeds enforced limit {max_gpu_memory_bytes} bytes.",
            )
    return KernelResourcePolicyResult()


__all__ = ["KernelResourcePolicyResult", "evaluate_kernel_resource_policy"]
