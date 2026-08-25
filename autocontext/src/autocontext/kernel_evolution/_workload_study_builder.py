"""Aggregate construction for kernel workload study reports."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from autocontext.kernel_evolution.workload_study_validation import assess_champion, protocol_burns

if TYPE_CHECKING:
    from autocontext.kernel_evolution.workload_study import (
        KernelTransferEvidence,
        KernelWorkloadRunEvidence,
        KernelWorkloadSpec,
        KernelWorkloadStudyProvenance,
    )


def workload_study_report_payload(
    *,
    study_name: str,
    provenance: KernelWorkloadStudyProvenance,
    specs: tuple[KernelWorkloadSpec, ...],
    runs: tuple[KernelWorkloadRunEvidence, ...],
    transfers: tuple[KernelTransferEvidence, ...],
    created_at: str | None,
) -> dict[str, object]:
    """Replay every aggregate field into an unvalidated report payload."""
    workload_ids = tuple(spec.workload_id for spec in specs)
    spec_by_id = {spec.workload_id: spec for spec in specs}
    assessments = tuple(
        assess_champion(
            run,
            workload_ids=workload_ids,
            required_dimensions=set(spec_by_id[run.workload_id].required_transfer_dimensions),
            transfers=transfers,
        )
        for run in runs
        if run.disposition == "promoted"
    )
    portable = tuple(item.candidate_artifact_digest for item in assessments if item.disposition == "portable")
    lessons = tuple(
        f"{item.source_workload_id} champion transferred to "
        f"{', '.join(workload for workload in item.passed_workload_ids if workload != item.source_workload_id)}"
        for item in assessments
        if len(item.passed_workload_ids) > 1
    )
    regressions = tuple(
        f"{item.source_workload_id} champion failed required evidence on {', '.join(item.failed_workload_ids)}"
        for item in assessments
        if item.failed_workload_ids
    )
    plateaus = tuple(
        f"{run.workload_id} exhausted its bounded proposals without a promotion"
        for run in runs
        if run.disposition == "plateau"
    )
    incomplete = tuple(run.workload_id for run in runs if run.disposition == "incomplete")
    transfer_wall = sum(item.wall_seconds for item in transfers)
    transfer_cost = sum(item.cost_usd for item in transfers)
    return {
        "study_name": study_name,
        "created_at": created_at or datetime.now(UTC).isoformat(),
        "provenance": provenance,
        "workload_specs": specs,
        "workload_runs": runs,
        "transfers": transfers,
        "protocol_burns": protocol_burns(specs, runs, transfers),
        "champion_assessments": assessments,
        "portable_champion_artifact_digests": portable,
        "transferable_lessons": lessons,
        "regressions": regressions,
        "plateaus": plateaus,
        "incomplete_workloads": incomplete,
        "all_workloads_independently_verified": all(run.independently_verified for run in runs),
        "total_transfer_wall_seconds": transfer_wall,
        "total_transfer_cost_usd": transfer_cost,
        "total_wall_seconds": sum(float(run.total_wall_seconds) for run in runs) + transfer_wall,
        "total_cost_usd": sum(float(run.total_cost_usd) for run in runs) + transfer_cost,
    }


__all__ = ["workload_study_report_payload"]
