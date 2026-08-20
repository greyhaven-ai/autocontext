"""Event replay reducer for the durable campaign scheduler."""

from __future__ import annotations

from typing import Any

from autocontext.execution import campaign_scheduler_cancellation as cancellation
from autocontext.execution.campaign_scheduler_codecs import (
    as_float,
    budget_from,
    evidence_from,
    job_from,
    lease_from,
    mapping,
    result_from,
    result_to_dict,
    sequence,
    worker_from,
)
from autocontext.execution.campaign_scheduler_models import (
    JobOutcome,
    JobStatus,
    SchedulerBudget,
    SchedulerEvent,
    _JobState,
    _WorkerState,
    replace_lease_expiry,
)


def apply_scheduler_event(scheduler: Any, event: SchedulerEvent) -> None:
    """Apply one checksummed event to scheduler-owned in-memory state."""

    scheduler._sequence = event.sequence
    payload = event.payload
    if event.event_type == "campaign_configured":
        campaign_id = str(payload["campaign_id"])
        scheduler._campaign_limits[campaign_id] = budget_from(payload["budget"])
        branch_budgets = mapping(payload.get("branch_budgets", {}))
        for branch, budget in branch_budgets.items():
            scheduler._branch_limits[(campaign_id, branch)] = budget_from(budget)
        return
    if event.event_type == "runtime_plan_bound":
        scheduler._campaign_plan_fingerprints[str(payload["campaign_id"])] = str(payload["fingerprint"])
        return
    if event.event_type == "audit_checkpoint_failed":
        campaign_id = str(payload["campaign_id"])
        scheduler._audit_failures.setdefault(campaign_id, []).append(
            {
                "checkpoint": str(payload["checkpoint"]),
                "failure": str(payload["failure"]),
            }
        )
        return
    if event.event_type == "worker_registered":
        descriptor = worker_from(payload["worker"])
        prior = scheduler._workers.get(descriptor.worker_id)
        scheduler._workers[descriptor.worker_id] = _WorkerState(
            descriptor=descriptor,
            last_heartbeat=as_float(payload["at"]),
            active_leases=set(prior.active_leases) if prior else set(),
            completed_jobs=prior.completed_jobs if prior else 0,
            failed_jobs=prior.failed_jobs if prior else 0,
            cleanup_succeeded=prior.cleanup_succeeded if prior else 0,
            cleanup_failed=prior.cleanup_failed if prior else 0,
            consumed=prior.consumed if prior else SchedulerBudget(),
        )
        return
    if event.event_type == "worker_heartbeat":
        worker = scheduler._workers[str(payload["worker_id"])]
        worker.last_heartbeat = as_float(payload["at"])
        for lease_id in sequence(payload.get("lease_ids", [])):
            job_id = scheduler._leases.get(str(lease_id))
            if job_id is None:
                continue
            lease = scheduler._jobs[job_id].lease
            if lease is not None and scheduler._jobs[job_id].status == "leased" and lease.lease_id == str(lease_id):
                scheduler._jobs[job_id].lease = replace_lease_expiry(
                    lease,
                    as_float(payload["expires_at"]),
                )
        return
    if event.event_type == "evidence_granted":
        grant = evidence_from(payload["grant"])
        scheduler._evidence_grants[grant.grant_id] = grant
        scheduler._campaign_consumed[grant.campaign_id] = scheduler._campaign_consumed.get(
            grant.campaign_id,
            SchedulerBudget(),
        ).add(SchedulerBudget(shared_evidence_tokens=grant.token_cost))
        return
    if event.event_type == "job_enqueued":
        request = job_from(payload["job"])
        if request.job_id in scheduler._jobs:
            raise ValueError(f"duplicate campaign job_id in event log: {request.job_id}")
        idempotency_key = (request.campaign_id, request.idempotency_key)
        if idempotency_key in scheduler._idempotency:
            raise ValueError("duplicate campaign idempotency key in event log")
        scheduler._jobs[request.job_id] = _JobState(request=request)
        scheduler._idempotency[idempotency_key] = request.job_id
        if request.cohort_id:
            scheduler._cohort_lanes[(request.campaign_id, request.cohort_id)] = request.lane.digest
        return
    if event.event_type == "warm_degraded":
        return
    if event.event_type == "job_leased":
        lease = lease_from(payload["lease"])
        state = scheduler._jobs[lease.job_id]
        state.status = "leased"
        state.attempts = lease.attempt
        state.lease = lease
        scheduler._leases[lease.lease_id] = lease.job_id
        scheduler._lease_history[lease.lease_id] = lease.job_id
        scheduler._lease_records[lease.lease_id] = lease
        scheduler._workers[lease.worker_id].active_leases.add(lease.lease_id)
        scheduler._reserve(state.request)
        if state.request.cohort_id:
            scheduler._cohort_environments[(state.request.campaign_id, state.request.cohort_id)] = lease.environment_fingerprint
        return
    if event.event_type == "job_budget_exhausted":
        scheduler._jobs[str(payload["job_id"])].status = "budget_exhausted"
        return
    if event.event_type == "job_cancel_requested":
        scheduler._jobs[str(payload["job_id"])].status = "canceling"
        return
    if event.event_type in {"job_canceled_late", "job_stale_completed"}:
        state = scheduler._jobs[str(payload["job_id"])]
        result = result_from(payload["result"])
        lease_id = str(payload["lease_id"])
        historical_lease = scheduler._lease_records.get(lease_id)
        worker_id = historical_lease.worker_id if historical_lease else ""
        if state.lease is not None and state.lease.lease_id == lease_id:
            scheduler._release_lease(state)
        if event.event_type == "job_canceled_late":
            state.status = "canceled"
        state.late_result = result
        state.accounting_result = result
        scheduler._late_completion_leases.add(lease_id)
        scheduler._result_completion_leases.add(lease_id)
        provisional = scheduler._provisional_results.pop(lease_id, None)
        if provisional is None:
            scheduler._account_result(state.request, worker_id, result, failed=True)
        else:
            scheduler._replace_accounted_result(state.request, worker_id, provisional, result)
        scheduler._late_completions += 1
        return
    if event.event_type in {"job_requeued", "job_lease_failed", "job_canceled"}:
        state = scheduler._jobs[str(payload["job_id"])]
        active_lease = state.lease
        worker_id = active_lease.worker_id if active_lease else ""
        scheduler._release_lease(state)
        if event.event_type == "job_requeued":
            serialized_result = payload.get("result")
            provisional_usage = payload.get("provisional_usage") is True
            if serialized_result is None and payload.get("reason") == "lease_expired":
                serialized_result = result_to_dict(
                    cancellation.provisional_expired_lease_result(state.request, active_lease)
                )
                provisional_usage = True
            if serialized_result is not None:
                result = result_from(serialized_result)
                lease_id = str(payload["lease_id"])
                scheduler._result_completion_leases.add(lease_id)
                scheduler._account_result(state.request, worker_id, result, failed=True)
                if provisional_usage:
                    scheduler._provisional_results[lease_id] = result
            state.status = "queued"
            state.last_infrastructure_error = str(payload.get("reason", ""))
            scheduler._retry_count += 1
        elif event.event_type == "job_lease_failed":
            serialized_result = payload.get("result")
            provisional_usage = payload.get("provisional_usage") is True
            if serialized_result is None:
                serialized_result = result_to_dict(
                    cancellation.provisional_expired_lease_result(state.request, active_lease)
                )
                provisional_usage = True
            result = result_from(serialized_result)
            lease_id = str(payload["lease_id"])
            scheduler._result_completion_leases.add(lease_id)
            state.status = "infrastructure_failed"
            state.result = result
            scheduler._account_result(state.request, worker_id, result, failed=True)
            if provisional_usage:
                scheduler._provisional_results[lease_id] = result
        else:
            serialized_result = payload.get("result")
            if serialized_result is not None:
                result = result_from(serialized_result)
                lease_id = str(payload["lease_id"])
                scheduler._result_completion_leases.add(lease_id)
                scheduler._account_result(state.request, worker_id, result, failed=True)
                state.accounting_result = result
                if payload.get("provisional_usage") is True:
                    scheduler._provisional_results[lease_id] = result
            state.status = "canceled"
        return
    if event.event_type == "job_finished":
        state = scheduler._jobs[str(payload["job_id"])]
        result = result_from(payload["result"])
        scheduler._result_completion_leases.add(str(payload["lease_id"]))
        worker_id = state.lease.worker_id if state.lease else ""
        scheduler._release_lease(state)
        state.result = result
        status_by_outcome: dict[JobOutcome, JobStatus] = {
            "candidate_success": "succeeded",
            "candidate_failure": "candidate_failed",
            "infrastructure_failure": "infrastructure_failed",
        }
        state.status = status_by_outcome[result.outcome]
        scheduler._account_result(
            state.request,
            worker_id,
            result,
            failed=result.outcome != "candidate_success",
        )


__all__ = ["apply_scheduler_event"]
