"""Audit checkpoint boundary for the live campaign scheduler."""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from typing import Any, Literal, Protocol

from autocontext.execution.campaign_scheduler_codecs import result_to_dict
from autocontext.execution.campaign_scheduler_models import (
    CampaignAssignment,
    CampaignJobResult,
    SchedulerBudget,
    _JobState,
)

SchedulerAuditCheckpoint = Literal[
    "pre_promotion",
    "inconclusive_gate",
    "integrity_alert",
    "final_completion",
]


class SchedulerAuditRunner(Protocol):
    def review_checkpoint(
        self,
        checkpoint: SchedulerAuditCheckpoint,
        evidence: Mapping[str, Any],
        *,
        cancellation_event: threading.Event | None = None,
    ) -> Any: ...


def review_scheduler_checkpoint(
    runner: SchedulerAuditRunner | None,
    checkpoint: SchedulerAuditCheckpoint,
    evidence: Mapping[str, Any],
) -> None:
    """Run an advisory audit without allowing it to rewrite scheduler state."""

    if runner is None:
        return
    try:
        runner.review_checkpoint(checkpoint, evidence)
    except Exception:
        # Deterministic scheduling and scoring remain authoritative when an
        # audit transport, packet factory, or provider fails.
        return


def durable_audit_records(
    runner: SchedulerAuditRunner | None,
    campaign_ids: Sequence[str],
) -> dict[str, tuple[Mapping[str, object], ...]]:
    """Read durable audit/disposition records from the standard runner."""

    if runner is None:
        return {}
    auditor = getattr(runner, "auditor", None)
    store = getattr(auditor, "store", None)
    records = getattr(store, "records", None)
    if not callable(records):
        return {}
    return {campaign_id: tuple(record.to_dict() for record in records(campaign_id)) for campaign_id in sorted(set(campaign_ids))}


def integrity_evidence(
    assignment: CampaignAssignment,
    result: CampaignJobResult,
    *,
    source: str,
) -> dict[str, Any]:
    return {
        "campaign_id": assignment.job.campaign_id,
        "job_id": assignment.job.job_id,
        "branch_id": assignment.job.branch_id,
        "lease_id": assignment.lease.lease_id,
        "attempt": assignment.lease.attempt,
        "worker_id": assignment.lease.worker_id,
        "source": source,
        "result": result_to_dict(result),
    }


def final_summary_evidence(
    campaign_id: str,
    jobs: Sequence[_JobState],
    consumed: SchedulerBudget,
) -> dict[str, Any]:
    return {
        "campaign_id": campaign_id,
        "consumed": asdict(consumed),
        "jobs": [
            {
                "job_id": state.request.job_id,
                "branch_id": state.request.branch_id,
                "status": state.status,
                "attempts": state.attempts,
                "scored_result": result_to_dict(state.result) if state.result is not None else None,
                "unscored_late_result": (result_to_dict(state.late_result) if state.late_result is not None else None),
            }
            for state in sorted(jobs, key=lambda item: item.request.job_id)
        ],
    }


__all__ = [
    "SchedulerAuditCheckpoint",
    "SchedulerAuditRunner",
    "durable_audit_records",
    "final_summary_evidence",
    "integrity_evidence",
    "review_scheduler_checkpoint",
]
