"""Resource-aware, durable campaign scheduler (AC-979)."""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from typing import Any, Literal

from autocontext.execution import campaign_scheduler_cancellation as cancellation
from autocontext.execution import campaign_scheduler_dispatch as dispatch
from autocontext.execution import campaign_scheduler_replay as replay
from autocontext.execution.campaign_scheduler_adapters import (
    CallableCampaignWorker,
    RemoteCampaignWorker,
    campaign_result_from_remote,
)
from autocontext.execution.campaign_scheduler_audits import (
    SchedulerAuditRunner,
    durable_audit_records,
    final_summary_evidence,
    integrity_evidence,
    review_scheduler_checkpoint,
)
from autocontext.execution.campaign_scheduler_codecs import job_to_dict, result_to_dict, worker_to_dict
from autocontext.execution.campaign_scheduler_models import (
    TERMINAL_STATUSES,
    AssignmentLifecycle,
    CampaignAssignment,
    CampaignBatchWorker,
    CampaignEvidenceGrant,
    CampaignJobRequest,
    CampaignJobResult,
    CampaignLease,
    CampaignSchedulerReport,
    CampaignWorker,
    CancellableCampaignWorker,
    EvaluationLaneIdentity,
    JobOutcome,
    JobStatus,
    SchedulerBudget,
    SchedulerEvent,
    SchedulerResources,
    WorkerDescriptor,
    _JobState,
    _WorkerState,
    finite_number,
    reuse_key,
)
from autocontext.execution.campaign_scheduler_store import CampaignSchedulerEventStore, StaleCampaignSchedulerError


class CampaignScheduler:
    """Provider-neutral lease scheduler with deterministic event replay."""

    def __init__(
        self,
        store: CampaignSchedulerEventStore,
        *,
        lease_seconds: float = 30.0,
        max_concurrency: int = 8,
        clock: Callable[[], float] = time.time,
        audit_checkpoints: SchedulerAuditRunner | None = None,
    ) -> None:
        if (
            isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, (int, float))
            or not finite_number(lease_seconds, minimum=0.0, inclusive=False)
        ):
            raise ValueError("lease_seconds must be a positive finite number")
        if isinstance(max_concurrency, bool) or not isinstance(max_concurrency, int) or max_concurrency < 1:
            raise ValueError("lease_seconds and max_concurrency must be positive")
        self.store = store
        self.lease_seconds = lease_seconds
        self.max_concurrency = max_concurrency
        self.clock = clock
        self.audit_checkpoints = audit_checkpoints
        self._sequence = 0
        self._jobs: dict[str, _JobState] = {}
        self._idempotency: dict[tuple[str, str], str] = {}
        self._leases: dict[str, str] = {}
        self._lease_history: dict[str, str] = {}
        self._lease_records: dict[str, CampaignLease] = {}
        self._workers: dict[str, _WorkerState] = {}
        self._executors: dict[str, CampaignWorker] = {}
        self._campaign_limits: dict[str, SchedulerBudget] = {}
        self._branch_limits: dict[tuple[str, str], SchedulerBudget] = {}
        self._campaign_reserved: dict[str, SchedulerBudget] = {}
        self._campaign_consumed: dict[str, SchedulerBudget] = {}
        self._campaign_cleanup: dict[str, dict[str, int]] = {}
        self._campaign_plan_fingerprints: dict[str, str] = {}
        self._audit_failures: dict[str, list[dict[str, str]]] = {}
        self._branch_reserved: dict[tuple[str, str], SchedulerBudget] = {}
        self._branch_consumed: dict[tuple[str, str], SchedulerBudget] = {}
        self._cohort_lanes: dict[tuple[str, str], str] = {}
        self._cohort_environments: dict[tuple[str, str], str] = {}
        self._evidence_grants: dict[str, CampaignEvidenceGrant] = {}
        self._retry_count = 0
        self._late_completions = 0
        self._late_completion_leases: set[str] = set()
        self._result_completion_leases: set[str] = set()
        self._provisional_results: dict[str, CampaignJobResult] = {}
        self._lock = threading.RLock()
        for event in self.store.read():
            self._apply(event)

    def configure_campaign(
        self,
        campaign_id: str,
        budget: SchedulerBudget,
        *,
        branch_budgets: Mapping[str, SchedulerBudget] | None = None,
    ) -> None:
        if not campaign_id.strip():
            raise ValueError("campaign_id must be non-empty")
        with self._lock:
            self._record(
                "campaign_configured",
                {
                    "campaign_id": campaign_id,
                    "budget": asdict(budget),
                    "branch_budgets": {branch: asdict(limit) for branch, limit in (branch_budgets or {}).items()},
                },
            )

    def bind_runtime_plan(self, campaign_id: str, fingerprint: str) -> None:
        """Bind a durable campaign to one immutable runtime-plan identity."""

        if not campaign_id.strip() or not fingerprint.strip():
            raise ValueError("campaign runtime-plan identity must be non-empty")
        with self._lock:
            existing = self._campaign_plan_fingerprints.get(campaign_id)
            if existing is not None:
                if existing != fingerprint:
                    raise ValueError("campaign scheduler runtime plan conflicts with durable plan identity")
                return
            self._record("runtime_plan_bound", {"campaign_id": campaign_id, "fingerprint": fingerprint})

    def register_worker(self, descriptor: WorkerDescriptor, executor: CampaignWorker | None = None) -> None:
        with self._lock:
            current = self._workers.get(descriptor.worker_id)
            if current is None or current.descriptor != descriptor:
                self._record("worker_registered", {"worker": worker_to_dict(descriptor), "at": self.clock()})
            if executor is not None:
                self._executors[descriptor.worker_id] = executor

    def heartbeat(self, worker_id: str, lease_ids: Sequence[str] = ()) -> None:
        with self._lock:
            self._require_worker(worker_id)
            now = self.clock()
            valid: list[str] = []
            for lease_id in lease_ids:
                job_id = self._leases.get(lease_id)
                if job_id is None:
                    continue
                lease = self._jobs[job_id].lease
                if lease is not None and lease.worker_id == worker_id and self._jobs[job_id].status == "leased":
                    valid.append(lease_id)
            self._record(
                "worker_heartbeat",
                {
                    "worker_id": worker_id,
                    "at": now,
                    "lease_ids": valid,
                    "expires_at": now + self.lease_seconds,
                },
            )

    def grant_evidence_share(self, grant: CampaignEvidenceGrant) -> None:
        if grant.token_cost < 0:
            raise ValueError("evidence token cost cannot be negative")
        with self._lock:
            if grant.grant_id in self._evidence_grants:
                return
            limit = self._campaign_limits.get(grant.campaign_id)
            consumed = self._campaign_consumed.get(grant.campaign_id, SchedulerBudget())
            reserved = self._campaign_reserved.get(grant.campaign_id, SchedulerBudget())
            proposed = consumed.add(reserved).add(SchedulerBudget(shared_evidence_tokens=grant.token_cost))
            if limit is not None and not proposed.within(limit):
                raise RuntimeError("campaign shared-evidence budget exhausted")
            self._record("evidence_granted", {"grant": asdict(grant)})

    def enqueue(self, request: CampaignJobRequest) -> str:
        with self._lock:
            duplicate = self._idempotency.get((request.campaign_id, request.idempotency_key))
            if duplicate is not None:
                if self._jobs[duplicate].request != request:
                    raise ValueError("campaign idempotency key conflicts with a different durable job request")
                return duplicate
            if request.job_id in self._jobs:
                raise ValueError(f"campaign job_id already exists: {request.job_id}")
            cohort_key = (request.campaign_id, request.cohort_id)
            if request.cohort_id:
                lane = self._cohort_lanes.get(cohort_key)
                if lane is not None and lane != request.lane.digest:
                    raise ValueError("matched cohort jobs must use the same evaluation lane identity")
            self._validate_evidence_grants(request)
            self._record("job_enqueued", {"job": job_to_dict(request)})
            return request.job_id

    def claim(self, worker_id: str, *, limit: int | None = None) -> tuple[CampaignAssignment, ...]:
        with self._lock:
            worker = self._require_worker(worker_id)
            available_slots = worker.descriptor.concurrency - len(worker.active_leases)
            global_slots = self.max_concurrency - sum(len(state.active_leases) for state in self._workers.values())
            take = max(0, min(available_slots, global_slots, limit if limit is not None else available_slots))
            assignments: list[CampaignAssignment] = []
            for state in self._jobs.values():
                if len(assignments) >= take:
                    break
                if state.status != "queued" or not self._worker_matches(worker, state.request):
                    continue
                budget_state = self._budget_state(state.request)
                if budget_state == "exhausted":
                    self._record("job_budget_exhausted", {"job_id": state.request.job_id})
                    continue
                if budget_state == "reserved":
                    continue
                now = self.clock()
                lifecycle = self._select_lifecycle(worker.descriptor, state.request)
                if state.request.prefer_warm_reuse and lifecycle == "ephemeral_per_eval":
                    self._record(
                        "warm_degraded",
                        {"job_id": state.request.job_id, "worker_id": worker_id, "reason": "capability_unavailable"},
                    )
                lease = CampaignLease(
                    lease_id=str(uuid.uuid4()),
                    job_id=state.request.job_id,
                    worker_id=worker_id,
                    attempt=state.attempts + 1,
                    issued_at=now,
                    expires_at=now + self.lease_seconds,
                    environment_fingerprint=worker.descriptor.environment_fingerprint,
                    lifecycle=lifecycle,
                    reuse_key=reuse_key(state.request),
                )
                self._record("job_leased", {"lease": asdict(lease), "reservation": asdict(state.request.reservation)})
                assignments.append(CampaignAssignment(job=state.request, lease=lease))
            return tuple(assignments)

    def complete(self, lease_id: str, result: CampaignJobResult) -> CampaignJobResult:
        with self._lock:
            job_id = self._leases.get(lease_id)
            if job_id is None:
                historical_job = self._lease_history.get(lease_id)
                if historical_job is not None:
                    historical_state = self._jobs[historical_job]
                    completed = historical_state.result
                    if lease_id in self._result_completion_leases:
                        if lease_id not in self._provisional_results:
                            return completed if completed is not None else result
                    event_type = "job_canceled_late" if historical_state.status == "canceled" else "job_stale_completed"
                    self._record(
                        event_type,
                        {"job_id": historical_job, "lease_id": lease_id, "result": result_to_dict(result)},
                    )
                    return completed if completed is not None else result
                raise ValueError("lease is stale or unknown")
            state = self._jobs[job_id]
            if state.status == "canceling" and state.lease is not None and state.lease.lease_id == lease_id:
                self._record(
                    "job_canceled_late",
                    {"job_id": job_id, "lease_id": lease_id, "result": result_to_dict(result)},
                )
                return result
            if state.status != "leased" or state.lease is None or state.lease.lease_id != lease_id:
                if state.result is not None:
                    return state.result
                raise ValueError("lease is no longer active")
            assignment = CampaignAssignment(job=state.request, lease=state.lease)
            if result.outcome == "infrastructure_failure" and state.attempts < state.request.max_attempts:
                self._record(
                    "job_requeued",
                    {
                        "job_id": job_id,
                        "lease_id": lease_id,
                        "reason": result.detail or "infrastructure_failure",
                        "result": result_to_dict(result),
                    },
                )
            else:
                self._record(
                    "job_finished",
                    {"job_id": job_id, "lease_id": lease_id, "result": result_to_dict(result)},
                )
        if result.outcome == "infrastructure_failure":
            failure = review_scheduler_checkpoint(
                self.audit_checkpoints,
                "integrity_alert",
                integrity_evidence(assignment, result, source="worker_completion"),
            )
            if failure is not None:
                self._record_audit_failure(assignment.job.campaign_id, "integrity_alert", failure)
        return result

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            state = self._jobs.get(job_id)
            if state is None or state.status in TERMINAL_STATUSES:
                return False
            if state.status == "queued":
                self._record("job_canceled", {"job_id": job_id, "reason": "canceled_before_dispatch"})
                return True
            if state.status == "canceling":
                return True
            if state.lease is None:
                return False
            assignment = CampaignAssignment(job=state.request, lease=state.lease)
            executor = self._executors.get(state.lease.worker_id)
            self._record("job_cancel_requested", {"job_id": job_id, "lease_id": state.lease.lease_id})
        cancel_worker = getattr(executor, "cancel", None)
        cancellation_confirmed = bool(cancel_worker(assignment)) if callable(cancel_worker) else False
        if cancellation_confirmed:
            with self._lock:
                current = self._jobs[job_id]
                if current.status == "canceling":
                    self._record(
                        "job_canceled",
                        cancellation.cancellation_event_payload(
                            current.request,
                            assignment.lease,
                            "worker_acknowledged",
                        ),
                    )
        return True

    def reconcile(self, *, now: float | None = None) -> tuple[str, ...]:
        """Expire lost-worker leases and deterministically retry/fail their jobs."""
        with self._lock:
            current = self.clock() if now is None else now
            reconciled: list[str] = []
            audit_items: list[tuple[CampaignAssignment, CampaignJobResult]] = []
            for state in list(self._jobs.values()):
                lease = state.lease
                if state.status not in {"leased", "canceling"} or lease is None or lease.expires_at > current:
                    continue
                if state.status == "canceling":
                    self._record(
                        "job_canceled",
                        cancellation.cancellation_event_payload(
                            state.request,
                            lease,
                            "cancel_lease_expired",
                        ),
                    )
                    reconciled.append(state.request.job_id)
                    continue
                event_type = "job_requeued" if state.attempts < state.request.max_attempts else "job_lease_failed"
                provisional = cancellation.provisional_expired_lease_result(state.request, lease)
                self._record(
                    event_type,
                    {
                        "job_id": state.request.job_id,
                        "lease_id": lease.lease_id,
                        "reason": "lease_expired",
                        "result": result_to_dict(provisional),
                        "provisional_usage": True,
                    },
                )
                reconciled.append(state.request.job_id)
                if event_type == "job_lease_failed":
                    audit_items.append((CampaignAssignment(state.request, lease), provisional))
        for assignment, result in audit_items:
            failure = review_scheduler_checkpoint(
                self.audit_checkpoints,
                "integrity_alert",
                integrity_evidence(assignment, result, source="lease_reconciliation"),
            )
            if failure is not None:
                self._record_audit_failure(assignment.job.campaign_id, "integrity_alert", failure)
        return tuple(reconciled)

    def dispatch_once(self) -> int:
        """Claim available internal workers and execute one concurrent wave."""
        assignments, executors = self._claim_dispatch_wave()
        self._execute_dispatch_wave(assignments, executors)
        return len(assignments)

    def _claim_dispatch_wave(self) -> tuple[tuple[CampaignAssignment, ...], tuple[CampaignWorker, ...]]:
        assignments: list[CampaignAssignment] = []
        executors: list[CampaignWorker] = []
        with self._lock:
            global_available = self.max_concurrency - sum(len(worker.active_leases) for worker in self._workers.values())
            for worker_id in sorted(self._workers):
                executor = self._executors.get(worker_id)
                if executor is None or global_available <= 0:
                    continue
                claimed = self.claim(worker_id, limit=global_available)
                assignments.extend(claimed)
                executors.extend([executor] * len(claimed))
                global_available -= len(claimed)
        return tuple(assignments), tuple(executors)

    def _execute_dispatch_wave(
        self,
        assignments: tuple[CampaignAssignment, ...],
        executors: tuple[CampaignWorker, ...],
    ) -> None:
        for assignment, result in dispatch.execute_assignment_groups(assignments, executors, self._heartbeat_assignments):
            self.complete(assignment.lease.lease_id, result)

    def serve(
        self,
        stop_event: threading.Event,
        *,
        poll_interval: float = 0.25,
        cancel_grace_seconds: float = 1.0,
    ) -> int:
        """Run a restart-safe live dispatch loop until ``stop_event`` is set."""
        if (
            not finite_number(poll_interval, minimum=0.0, inclusive=False)
            or not finite_number(cancel_grace_seconds, minimum=0.0, inclusive=True)
        ):
            raise ValueError("poll_interval must be positive and cancel_grace_seconds cannot be negative")
        try:
            return dispatch.run_scheduler_service(
                stop_event,
                poll_interval=poll_interval,
                cancel_grace_seconds=cancel_grace_seconds,
                reconcile=self.reconcile,
                claim_wave=self._claim_dispatch_wave,
                execute_wave=self._execute_dispatch_wave,
                cancel_active=self._cancel_active_jobs,
            )
        finally:
            # ``stop_event`` requests scheduler shutdown; it does not cancel the
            # terminal integrity review. The auditor has its own hard deadline.
            self._audit_final_summaries()

    def _cancel_active_jobs(self, grace_seconds: float, poll_interval: float) -> None:
        cancellation.cancel_active_jobs(self, grace_seconds, poll_interval)

    def run_until_idle(
        self,
        *,
        max_waves: int = 100,
        poll_interval: float = 0.05,
        timeout_seconds: float = 300.0,
        stop_event: threading.Event | None = None,
        cancel_grace_seconds: float = 1.0,
    ) -> int:
        if isinstance(max_waves, bool) or not isinstance(max_waves, int) or max_waves < 1:
            raise ValueError("campaign scheduler drain limits are invalid")
        if not all(
            (
                finite_number(poll_interval, minimum=0.0, inclusive=False),
                finite_number(timeout_seconds, minimum=0.0, inclusive=False),
                finite_number(cancel_grace_seconds, minimum=0.0, inclusive=True),
            )
        ):
            raise ValueError("campaign scheduler drain limits are invalid")
        try:
            return dispatch.run_scheduler_until_idle(
                max_waves=max_waves,
                poll_interval=poll_interval,
                timeout_seconds=timeout_seconds,
                cancel_grace_seconds=cancel_grace_seconds,
                stop_event=stop_event or threading.Event(),
                reconcile=self.reconcile,
                claim_wave=self._claim_dispatch_wave,
                execute_wave=self._execute_dispatch_wave,
                work_counts=self._work_counts,
                cancel_active=self._cancel_active_jobs,
            )
        finally:
            self._audit_final_summaries()

    def _work_counts(self) -> tuple[int, int]:
        with self._lock:
            queued = sum(state.status == "queued" for state in self._jobs.values())
            running = sum(state.status in {"leased", "canceling"} for state in self._jobs.values())
            return queued, running

    def job_status(self, job_id: str) -> JobStatus:
        with self._lock:
            return self._jobs[job_id].status

    def job_result(self, job_id: str) -> CampaignJobResult | None:
        with self._lock:
            return self._jobs[job_id].result

    def report(self) -> CampaignSchedulerReport:
        with self._lock:
            counts = {status: 0 for status in TERMINAL_STATUSES | {"queued", "leased", "canceling"}}
            for state in self._jobs.values():
                counts[state.status] += 1
            utilization = {
                worker_id: {
                    "runtime": state.descriptor.runtime,
                    "locality": state.descriptor.locality,
                    "active_leases": len(state.active_leases),
                    "concurrency": state.descriptor.concurrency,
                    "completed_jobs": state.completed_jobs,
                    "failed_jobs": state.failed_jobs,
                    "cleanup_succeeded": state.cleanup_succeeded,
                    "cleanup_failed": state.cleanup_failed,
                    "consumed": asdict(state.consumed),
                    "last_heartbeat": state.last_heartbeat,
                }
                for worker_id, state in sorted(self._workers.items())
            }
            return CampaignSchedulerReport(
                queued=counts["queued"],
                running=counts["leased"] + counts["canceling"],
                succeeded=counts["succeeded"],
                candidate_failed=counts["candidate_failed"],
                infrastructure_failed=counts["infrastructure_failed"],
                budget_exhausted=counts["budget_exhausted"],
                canceled=counts["canceled"],
                late_completions=self._late_completions,
                retries=self._retry_count,
                reserved_by_campaign=dict(self._campaign_reserved),
                consumed_by_campaign=dict(self._campaign_consumed),
                consumed_by_branch={
                    campaign_id: {
                        branch_id: budget
                        for (candidate_campaign, branch_id), budget in sorted(self._branch_consumed.items())
                        if candidate_campaign == campaign_id
                    }
                    for campaign_id in sorted(self._campaign_ids())
                },
                cleanup_by_campaign={
                    campaign_id: dict(outcomes) for campaign_id, outcomes in sorted(self._campaign_cleanup.items())
                },
                worker_utilization=utilization,
                events=self._sequence,
                audit_records_by_campaign=durable_audit_records(
                    self.audit_checkpoints,
                    tuple(self._campaign_ids()),
                ),
                audit_failures_by_campaign={
                    campaign_id: tuple(items) for campaign_id, items in sorted(self._audit_failures.items())
                },
            )

    def consumed_for_branch(self, campaign_id: str, branch_id: str) -> SchedulerBudget:
        with self._lock:
            return self._branch_consumed.get((campaign_id, branch_id), SchedulerBudget())

    def _campaign_ids(self) -> set[str]:
        return set(self._campaign_limits) | {state.request.campaign_id for state in self._jobs.values()}

    def _audit_final_summaries(self, cancellation_event: threading.Event | None = None) -> None:
        with self._lock:
            evidence = []
            for campaign_id in sorted(self._campaign_ids()):
                jobs = [state for state in self._jobs.values() if state.request.campaign_id == campaign_id]
                if jobs and all(state.status in TERMINAL_STATUSES for state in jobs):
                    evidence.append(
                        final_summary_evidence(
                            campaign_id,
                            jobs,
                            self._campaign_consumed.get(campaign_id, SchedulerBudget()),
                        )
                    )
        for packet in evidence:
            failure = review_scheduler_checkpoint(self.audit_checkpoints, "final_completion", packet, cancellation_event)
            if failure is not None:
                self._record_audit_failure(str(packet["campaign_id"]), "final_completion", failure)

    def _record_audit_failure(self, campaign_id: str, checkpoint: str, failure: str) -> None:
        self._record(
            "audit_checkpoint_failed",
            {"campaign_id": campaign_id, "checkpoint": checkpoint, "failure": failure},
        )

    def _record(self, event_type: str, payload: Mapping[str, Any]) -> None:
        with self._lock:
            event = SchedulerEvent(
                sequence=self._sequence + 1,
                event_id=str(uuid.uuid4()),
                timestamp=self.clock(),
                event_type=event_type,
                payload=dict(payload),
            )
            self.store.append(event)
            self._apply(event)

    def _heartbeat_assignments(
        self,
        assignments: tuple[CampaignAssignment, ...],
        stop_event: threading.Event,
    ) -> None:
        if not assignments:
            return
        worker_id = assignments[0].lease.worker_id
        lease_ids = tuple(assignment.lease.lease_id for assignment in assignments)
        interval = max(0.01, self.lease_seconds / 3.0)
        while not stop_event.wait(interval):
            with self._lock:
                active = tuple(
                    lease_id
                    for lease_id in lease_ids
                    if (job_id := self._leases.get(lease_id)) is not None and self._jobs[job_id].status == "leased"
                )
            if not active:
                return
            self.heartbeat(worker_id, active)

    def _apply(self, event: SchedulerEvent) -> None:
        replay.apply_scheduler_event(self, event)

    def _worker_matches(self, worker: _WorkerState, request: CampaignJobRequest) -> bool:
        descriptor = worker.descriptor
        used = SchedulerResources(cpu_cores=0, memory_gb=0, disk_gb=0)
        for lease_id in worker.active_leases:
            job_id = self._leases.get(lease_id)
            if job_id:
                used = used.add(self._jobs[job_id].request.resources)
        available = descriptor.resources.subtract(used)
        if not available.fits(request.resources):
            return False
        if not request.required_capabilities.issubset(descriptor.capabilities):
            return False
        if request.cohort_id:
            pinned = self._cohort_environments.get((request.campaign_id, request.cohort_id))
            if pinned is not None and pinned != descriptor.environment_fingerprint:
                return False
        return True

    def _budget_state(self, request: CampaignJobRequest) -> Literal["available", "reserved", "exhausted"]:
        """Return available, reserved (transient), or exhausted (permanent)."""
        temporarily_reserved = False
        campaign_limit = self._campaign_limits.get(request.campaign_id)
        if campaign_limit is not None:
            consumed = self._campaign_consumed.get(request.campaign_id, SchedulerBudget())
            if not consumed.add(request.reservation).within(campaign_limit):
                return "exhausted"
            projected = self._campaign_reserved.get(request.campaign_id, SchedulerBudget()).add(consumed).add(request.reservation)
            if not projected.within(campaign_limit):
                temporarily_reserved = True
        branch_key = (request.campaign_id, request.branch_id)
        branch_limit = self._branch_limits.get(branch_key)
        if branch_limit is not None:
            consumed = self._branch_consumed.get(branch_key, SchedulerBudget())
            if not consumed.add(request.reservation).within(branch_limit):
                return "exhausted"
            projected = self._branch_reserved.get(branch_key, SchedulerBudget()).add(consumed).add(request.reservation)
            if not projected.within(branch_limit):
                temporarily_reserved = True
        return "reserved" if temporarily_reserved else "available"

    def _reserve(self, request: CampaignJobRequest) -> None:
        campaign = request.campaign_id
        branch = (request.campaign_id, request.branch_id)
        self._campaign_reserved[campaign] = self._campaign_reserved.get(campaign, SchedulerBudget()).add(request.reservation)
        self._branch_reserved[branch] = self._branch_reserved.get(branch, SchedulerBudget()).add(request.reservation)

    def _release_lease(self, state: _JobState) -> None:
        lease = state.lease
        if lease is None:
            return
        self._leases.pop(lease.lease_id, None)
        worker = self._workers.get(lease.worker_id)
        if worker is not None:
            worker.active_leases.discard(lease.lease_id)
        request = state.request
        campaign = request.campaign_id
        branch = (request.campaign_id, request.branch_id)
        self._campaign_reserved[campaign] = self._campaign_reserved.get(campaign, SchedulerBudget()).subtract(request.reservation)
        self._branch_reserved[branch] = self._branch_reserved.get(branch, SchedulerBudget()).subtract(request.reservation)
        state.lease = None

    def _consume(self, request: CampaignJobRequest, consumed: SchedulerBudget) -> None:
        campaign = request.campaign_id
        branch = (request.campaign_id, request.branch_id)
        self._campaign_consumed[campaign] = self._campaign_consumed.get(campaign, SchedulerBudget()).add(consumed)
        self._branch_consumed[branch] = self._branch_consumed.get(branch, SchedulerBudget()).add(consumed)

    def _account_result(
        self,
        request: CampaignJobRequest,
        worker_id: str,
        result: CampaignJobResult,
        *,
        failed: bool,
    ) -> None:
        consumed = self._normalized_consumption(result)
        self._consume(request, consumed)
        self._record_cleanup(request.campaign_id, worker_id, result.cleanup_succeeded)
        if worker_id:
            worker = self._workers[worker_id]
            worker.completed_jobs += int(not failed)
            worker.failed_jobs += int(failed)
            worker.consumed = worker.consumed.add(consumed)

    @staticmethod
    def _normalized_consumption(result: CampaignJobResult) -> SchedulerBudget:
        consumed = result.consumed
        return consumed if consumed.jobs else consumed.add(SchedulerBudget(jobs=1))

    def _replace_accounted_result(
        self,
        request: CampaignJobRequest,
        worker_id: str,
        provisional: CampaignJobResult,
        actual: CampaignJobResult,
    ) -> None:
        """Replace one conservative lease-expiry charge with late actual usage."""

        prior = self._normalized_consumption(provisional)
        current = self._normalized_consumption(actual)
        campaign = request.campaign_id
        branch = (request.campaign_id, request.branch_id)
        self._campaign_consumed[campaign] = self._campaign_consumed.get(campaign, SchedulerBudget()).subtract(prior).add(current)
        self._branch_consumed[branch] = self._branch_consumed.get(branch, SchedulerBudget()).subtract(prior).add(current)
        cleanup = self._campaign_cleanup.setdefault(campaign, {"succeeded": 0, "failed": 0})
        prior_key = "succeeded" if provisional.cleanup_succeeded else "failed"
        cleanup[prior_key] = max(0, cleanup[prior_key] - 1)
        cleanup["succeeded" if actual.cleanup_succeeded else "failed"] += 1
        if worker_id:
            worker = self._workers[worker_id]
            worker.consumed = worker.consumed.subtract(prior).add(current)
            if provisional.cleanup_succeeded:
                worker.cleanup_succeeded = max(0, worker.cleanup_succeeded - 1)
            else:
                worker.cleanup_failed = max(0, worker.cleanup_failed - 1)
            if actual.cleanup_succeeded:
                worker.cleanup_succeeded += 1
            else:
                worker.cleanup_failed += 1

    def _record_cleanup(self, campaign_id: str, worker_id: str, succeeded: bool) -> None:
        outcomes = self._campaign_cleanup.setdefault(campaign_id, {"succeeded": 0, "failed": 0})
        outcomes["succeeded" if succeeded else "failed"] += 1
        if worker_id:
            worker = self._workers[worker_id]
            if succeeded:
                worker.cleanup_succeeded += 1
            else:
                worker.cleanup_failed += 1
    def _select_lifecycle(self, descriptor: WorkerDescriptor, request: CampaignJobRequest) -> AssignmentLifecycle:
        if not request.prefer_warm_reuse:
            return "ephemeral_per_eval"
        if {"warm", "snapshot"}.issubset(descriptor.sandbox_features):
            return "warm_snapshot"
        if "session_reuse" in descriptor.sandbox_features:
            return "reuse_matched_trials"
        return "ephemeral_per_eval"

    def _validate_evidence_grants(self, request: CampaignJobRequest) -> None:
        for grant_id in request.evidence_grant_ids:
            grant = self._evidence_grants.get(grant_id)
            if grant is None:
                raise ValueError(f"unknown campaign evidence grant: {grant_id}")
            if grant.campaign_id != request.campaign_id or grant.to_branch_id != request.branch_id:
                raise PermissionError(f"evidence grant is not scoped to branch {request.branch_id}: {grant_id}")

    def _require_worker(self, worker_id: str) -> _WorkerState:
        try:
            return self._workers[worker_id]
        except KeyError as exc:
            raise KeyError(f"unknown campaign worker: {worker_id}") from exc


__all__ = [
    "AssignmentLifecycle",
    "CallableCampaignWorker",
    "CampaignAssignment",
    "CampaignBatchWorker",
    "CancellableCampaignWorker",
    "CampaignEvidenceGrant",
    "CampaignJobRequest",
    "CampaignJobResult",
    "CampaignLease",
    "CampaignScheduler",
    "CampaignSchedulerEventStore",
    "CampaignSchedulerReport",
    "CampaignWorker",
    "EvaluationLaneIdentity",
    "JobOutcome",
    "JobStatus",
    "RemoteCampaignWorker",
    "SchedulerBudget",
    "SchedulerEvent",
    "SchedulerResources",
    "StaleCampaignSchedulerError",
    "WorkerDescriptor",
    "campaign_result_from_remote",
]
