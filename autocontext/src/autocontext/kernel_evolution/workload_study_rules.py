"""Intrinsic reservation and observation rules for kernel workload studies."""

from __future__ import annotations

from typing import TYPE_CHECKING

from autocontext.kernel_evolution.models import canonical_digest

if TYPE_CHECKING:
    from autocontext.kernel_evolution.generation import KernelGenerationFailure, KernelGenerationResult
    from autocontext.kernel_evolution.models import KernelBenchmarkObservation, KernelBenchmarkReport, KernelEvolutionResult
    from autocontext.kernel_evolution.workload_study import (
        KernelTransferEvidence,
        KernelTransferPhaseEvidence,
        KernelWorkloadPhaseEvidence,
        KernelWorkloadRunEvidence,
        KernelWorkloadSpec,
    )

_REPORT_DERIVED_OBSERVATION_FIELDS = (
    "hardware_scope_id",
    "baseline_id",
    "protocol_id",
    "protocol_compatibility_id",
    "derived_statistics_receipt",
    "candidate_median_ms",
    "incumbent_median_ms",
    "reference_median_ms",
    "speedup_vs_incumbent",
    "speedup_vs_reference",
    "speedup_lcb95",
    "speedup_lcb",
    "confidence_level",
    "all_case_no_regression_passed",
    "relative_improvement",
    "candidate_p95_ms",
    "incumbent_p95_ms",
    "environment_drift_ratio",
)
_CONCLUSIVE_CANDIDATE_FAILURES = {"syntax", "compile", "correctness", "candidate_crash"}


def kernel_generation_receipt_context_digest(
    *,
    study_execution_id: str,
    workload_spec_id: str,
    run_id: str,
    generation_budget_id: str,
    generation_results: tuple[KernelGenerationResult, ...],
    generation_failures: tuple[KernelGenerationFailure, ...],
) -> str:
    """Bind generation activity to exactly one study, workload, run, and budget."""
    return canonical_digest(
        {
            "schema_version": "autocontext.kernel-generation-receipt-context/v1",
            "study_execution_id": study_execution_id,
            "workload_spec_id": workload_spec_id,
            "run_id": run_id,
            "generation_budget_id": generation_budget_id,
            "generation_results": [item.model_dump(mode="json") for item in generation_results],
            "generation_failures": [item.model_dump(mode="json") for item in generation_failures],
        }
    )


def validate_reportless_observation(observation: KernelBenchmarkObservation) -> None:
    """Reject identities or derived statistics that have no persisted raw report."""
    if observation.report is None and any(
        getattr(observation, field) is not None for field in _REPORT_DERIVED_OBSERVATION_FIELDS
    ):
        raise ValueError("reportless phase observations cannot carry report-derived identities or statistics")


def validate_observation_policy(observation: KernelBenchmarkObservation, result: KernelEvolutionResult) -> None:
    """Bind report-backed or reportless evidence to its workload campaign policy."""
    report = observation.report
    policy = result.decision_policy
    if (
        policy is None
        or observation.statistics_policy != policy.statistics
        or (report is not None and report.protocol.sequential_testing != policy.sequential_testing)
    ):
        raise ValueError("study phase statistics policy disagrees with its workload decision policy")


def timing_boundaries_comparable(report: KernelBenchmarkReport, *, require_evidence: bool = False) -> bool:
    """Replay the trusted candidate/incumbent/reference timing boundary claim."""
    timing = report.metadata.get("timing_comparability")
    if not isinstance(timing, dict):
        return not require_evidence
    return (
        timing.get("candidate_incumbent_comparable") is True
        and timing.get("reference_comparable") is True
        and timing.get("promotion_comparison") == ["candidate_ms", "incumbent_ms"]
    )


def validate_phase_report_metadata(
    phase: KernelWorkloadPhaseEvidence | KernelTransferPhaseEvidence,
    *,
    workload_id: str,
    workload_family: str,
    source_workload_id: str,
    target_workload_id: str,
) -> None:
    """Bind optional descriptive report metadata to the typed phase route."""
    report = phase.observation.report
    if report is None:
        return
    expected = {
        "workload_id": workload_id,
        "workload_family": workload_family,
        "profile_role": phase.role,
        "source_workload_id": source_workload_id,
        "target_workload_id": target_workload_id,
    }
    if any(name in report.metadata and report.metadata[name] != value for name, value in expected.items()):
        raise ValueError("phase report metadata disagrees with its typed workload route")


def validate_run_phase_metadata(run: KernelWorkloadRunEvidence, spec: KernelWorkloadSpec) -> None:
    for phase in (run.primary, run.confirmation):
        validate_phase_report_metadata(
            phase,
            workload_id=run.workload_id,
            workload_family=spec.workload_family,
            source_workload_id=run.workload_id,
            target_workload_id=run.workload_id,
        )


def validate_transfer_phase_metadata(transfer: KernelTransferEvidence, target: KernelWorkloadSpec) -> None:
    for phase in (transfer.primary, transfer.confirmation):
        validate_phase_report_metadata(
            phase,
            workload_id=target.workload_id,
            workload_family=target.workload_family,
            source_workload_id=transfer.source_workload_id,
            target_workload_id=transfer.target_workload_id,
        )


def observation_conclusively_failed(observation: KernelBenchmarkObservation) -> bool:
    """Return whether persisted evidence proves a candidate-owned failure."""
    report = observation.report
    if report is None or report.evaluation_status == "infrastructure_error":
        return False
    if report.evaluation_status == "candidate_error":
        return report.failure_kind in _CONCLUSIVE_CANDIDATE_FAILURES
    if report.evaluation_status != "complete" or not observation.eligible:
        return False
    correctness_failed = report.correctness is not None and not report.correctness.passed
    case_floor_failed = report.performance is not None and any(
        not case.passed_no_regression for case in report.performance.cases
    )
    return correctness_failed or case_floor_failed


def validate_spec_reservations(spec: KernelWorkloadSpec) -> None:
    """Validate every published reservation, including routes never consumed."""
    if set(spec.required_correctness_slices) != {"train", "holdout"}:
        raise ValueError("workload correctness slices must be exactly train and holdout")
    if any(
        not any(case.startswith(f"{split}:") for case in spec.required_benchmark_cases)
        for split in ("train", "holdout")
    ):
        raise ValueError("workload benchmark cases must cover both train and holdout")
    standard = (
        spec.primary_protocol,
        *spec.confirmation_protocols,
        spec.final_primary_protocol,
        spec.final_confirmation_protocol,
    )
    if any(item.execution_environment_id != spec.execution_environment_id for item in standard):
        raise ValueError("campaign or final reservation used an unpinned execution environment")
    if any(item.hardware_scope_id != spec.primary_protocol.hardware_scope_id for item in standard):
        raise ValueError("campaign or final reservation changed the pinned hardware scope")
    for route in spec.transfer_protocols:
        route_environment = route.primary.execution_environment_id
        route_scope = route.primary.hardware_scope_id
        if route.source_workload_id == spec.workload_id:
            if route_environment == spec.execution_environment_id or route_scope == spec.primary_protocol.hardware_scope_id:
                raise ValueError("same-workload hardware transfer must cross its pinned hardware identity")
        elif "hardware" not in route.dimensions and (
            route_environment != spec.execution_environment_id or route_scope != spec.primary_protocol.hardware_scope_id
        ):
            raise ValueError("non-hardware transfer reservation must use the pinned target hardware scope")


__all__ = [
    "kernel_generation_receipt_context_digest",
    "observation_conclusively_failed",
    "timing_boundaries_comparable",
    "validate_observation_policy",
    "validate_reportless_observation",
    "validate_run_phase_metadata",
    "validate_spec_reservations",
    "validate_transfer_phase_metadata",
]
