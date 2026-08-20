"""Audit checkpoint boundary for the live campaign scheduler."""

from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)

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
    cancellation_event: threading.Event | None = None,
) -> str | None:
    """Run an advisory audit without allowing it to rewrite scheduler state."""

    if runner is None:
        return None
    try:
        runner.review_checkpoint(checkpoint, evidence, cancellation_event=cancellation_event)
    except Exception as exc:
        # Deterministic scheduling and scoring remain authoritative when an
        # audit transport, packet factory, or provider fails.
        failure = type(exc).__name__
        logger.warning("campaign audit checkpoint %s failed: %s", checkpoint, failure, exc_info=True)
        return failure
    return None


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
    # Keep audit imports lazy: the audit boundary depends on stable-digest
    # models whose package initialization imports scheduler public types.
    from autocontext.audit.campaign_audit_boundary import redacted_identity

    return {
        campaign_id: tuple(record.to_dict() for record in records(redacted_identity(campaign_id, "campaign")))
        for campaign_id in sorted(set(campaign_ids))
    }


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
        "candidate_digest": _request_candidate_digest(assignment.job),
        "cohort_id": assignment.job.cohort_id,
        "lane_id": assignment.job.lane.lane_id,
        "fixture_digest": assignment.job.lane.fixture_digest,
        "seeds": list(assignment.job.lane.seeds),
        "evaluator_epoch": assignment.job.lane.evaluator_epoch,
        "verifier_contract_ref": assignment.job.lane.verifier_contract_ref,
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
    statuses = {state.status for state in jobs}
    if statuses and statuses <= {"succeeded"}:
        terminal_state = "completed"
    elif "infrastructure_failed" in statuses or "candidate_failed" in statuses:
        terminal_state = "failed"
    elif "budget_exhausted" in statuses:
        terminal_state = "budget_exhausted"
    elif "canceled" in statuses:
        terminal_state = "canceled"
    else:
        terminal_state = "active"
    return {
        "campaign_id": campaign_id,
        "status": terminal_state,
        "consumed": asdict(consumed),
        "jobs": [
            {
                "job_id": state.request.job_id,
                "branch_id": state.request.branch_id,
                "candidate_digest": _candidate_digest(state),
                "cohort_id": state.request.cohort_id,
                "lane_id": state.request.lane.lane_id,
                "fixture_digest": state.request.lane.fixture_digest,
                "seeds": list(state.request.lane.seeds),
                "evaluator_epoch": state.request.lane.evaluator_epoch,
                "verifier_contract_ref": state.request.lane.verifier_contract_ref,
                "status": state.status,
                "attempts": state.attempts,
                "scored_result": result_to_dict(state.result) if state.result is not None else None,
                "accounting_result": (
                    result_to_dict(state.accounting_result) if state.accounting_result is not None else None
                ),
                "unscored_late_result": (result_to_dict(state.late_result) if state.late_result is not None else None),
            }
            for state in sorted(jobs, key=lambda item: item.request.job_id)
        ],
    }


def _candidate_digest(state: _JobState) -> str:
    return _request_candidate_digest(state.request)


def _request_candidate_digest(request: Any) -> str:
    from autocontext.context_bundles.models import stable_digest

    return stable_digest({"strategy": request.payload.get("strategy"), "job_kind": request.job_kind})


__all__ = [
    "SchedulerAuditCheckpoint",
    "SchedulerAuditRunner",
    "durable_audit_records",
    "final_summary_evidence",
    "integrity_evidence",
    "review_scheduler_checkpoint",
]
