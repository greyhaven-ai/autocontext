"""Fail-safe cancellation accounting for dispatched campaign leases."""

from __future__ import annotations

import threading
import time
from typing import Any

from autocontext.execution.campaign_scheduler_codecs import result_to_dict
from autocontext.execution.campaign_scheduler_models import (
    CampaignJobRequest,
    CampaignJobResult,
    CampaignLease,
    SchedulerBudget,
)


def cancellation_event_payload(
    request: CampaignJobRequest,
    lease: CampaignLease,
    reason: str,
) -> dict[str, Any]:
    """Build the durable provisional charge for an already-dispatched lease."""

    result = provisional_cancellation_result(request, lease, reason)
    return {
        "job_id": request.job_id,
        "lease_id": lease.lease_id,
        "reason": reason,
        "result": result_to_dict(result),
        "provisional_usage": True,
    }


def provisional_cancellation_result(
    request: CampaignJobRequest,
    _lease: CampaignLease,
    reason: str,
) -> CampaignJobResult:
    """Charge the full reservation until an eventual result replaces it."""

    reservation = request.reservation
    consumed = SchedulerBudget(
        tokens=reservation.tokens,
        wall_seconds=reservation.wall_seconds,
        compute_units=reservation.compute_units,
        jobs=max(1, reservation.jobs),
        shared_evidence_tokens=reservation.shared_evidence_tokens,
    )
    return CampaignJobResult(
        outcome="infrastructure_failure",
        consumed=consumed,
        detail=reason,
        cleanup_succeeded=False,
        metadata={"usage_estimated": True, "cancellation_provisional": True},
        retryable=False,
    )


def provisional_expired_lease_result(
    request: CampaignJobRequest,
    lease: CampaignLease | None,
) -> CampaignJobResult:
    """Conservatively account for an expired lease until a late result arrives."""

    reservation = request.reservation
    lease_wall = 0.0 if lease is None else max(0.0, lease.expires_at - lease.issued_at)
    return CampaignJobResult(
        outcome="infrastructure_failure",
        consumed=SchedulerBudget(
            tokens=reservation.tokens,
            wall_seconds=max(reservation.wall_seconds, lease_wall),
            compute_units=reservation.compute_units,
            jobs=max(1, reservation.jobs),
            shared_evidence_tokens=reservation.shared_evidence_tokens,
        ),
        detail="lease_expired",
        cleanup_succeeded=False,
        metadata={"usage_estimated": True},
        retryable=request.retry_expired_lease,
    )


def cancel_active_jobs(scheduler: Any, grace_seconds: float, poll_interval: float) -> None:
    """Request cancellation, then durably seal any unresolved dispatched work."""

    with scheduler._lock:
        active = [
            state.request.job_id
            for state in scheduler._jobs.values()
            if state.status in {"leased", "canceling"}
        ]
    for job_id in active:
        threading.Thread(target=scheduler.cancel, args=(job_id,), daemon=True).start()
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        scheduler.reconcile()
        with scheduler._lock:
            if all(scheduler._jobs[job_id].status not in {"leased", "canceling"} for job_id in active):
                return
        time.sleep(min(poll_interval, 0.05, max(0.0, deadline - time.monotonic())))
    with scheduler._lock:
        for job_id in active:
            state = scheduler._jobs[job_id]
            if state.status in {"leased", "canceling"} and state.lease is not None:
                scheduler._record(
                    "job_canceled",
                    cancellation_event_payload(state.request, state.lease, "cancel_grace_expired"),
                )


__all__ = [
    "cancel_active_jobs",
    "cancellation_event_payload",
    "provisional_cancellation_result",
    "provisional_expired_lease_result",
]
