"""Optional resource-aware campaign scheduler (AC-979).

The scheduler coordinates user-controlled local, trusted-host, and remote
workers. Its append-only event log is the source of truth for leases, retries,
budgets, comparable evaluation cohorts, utilization, and restart recovery.
"""

from __future__ import annotations

import concurrent.futures
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from typing import Any, Literal

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
from autocontext.execution.campaign_scheduler_codecs import (
    as_float,
    budget_from,
    evidence_from,
    job_from,
    job_to_dict,
    lease_from,
    mapping,
    result_from,
    result_to_dict,
    sequence,
    worker_from,
    worker_to_dict,
)
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
    replace_lease_expiry,
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
        if lease_seconds <= 0 or max_concurrency < 1:
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
        self._workers: dict[str, _WorkerState] = {}
        self._executors: dict[str, CampaignWorker] = {}
        self._campaign_limits: dict[str, SchedulerBudget] = {}
        self._branch_limits: dict[tuple[str, str], SchedulerBudget] = {}
        self._campaign_reserved: dict[str, SchedulerBudget] = {}
        self._campaign_consumed: dict[str, SchedulerBudget] = {}
        self._branch_reserved: dict[tuple[str, str], SchedulerBudget] = {}
        self._branch_consumed: dict[tuple[str, str], SchedulerBudget] = {}
        self._cohort_lanes: dict[tuple[str, str], str] = {}
        self._cohort_environments: dict[tuple[str, str], str] = {}
        self._evidence_grants: dict[str, CampaignEvidenceGrant] = {}
        self._retry_count = 0
        self._late_completions = 0
        self._late_completion_leases: set[str] = set()
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
                if lease is not None and lease.worker_id == worker_id:
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
            proposed = consumed.add(SchedulerBudget(shared_evidence_tokens=grant.token_cost))
            if limit is not None and not proposed.within(limit):
                raise RuntimeError("campaign shared-evidence budget exhausted")
            self._record("evidence_granted", {"grant": asdict(grant)})

    def enqueue(self, request: CampaignJobRequest) -> str:
        with self._lock:
            duplicate = self._idempotency.get((request.campaign_id, request.idempotency_key))
            if duplicate is not None:
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
                    if lease_id in self._late_completion_leases:
                        return result
                    if historical_state.status == "canceled":
                        self._record(
                            "job_canceled_late",
                            {"job_id": historical_job, "lease_id": lease_id, "result": result_to_dict(result)},
                        )
                        return result
                    # A worker can finish after cancellation, lease expiry, or a
                    # replacement attempt.  The result is deliberately ignored,
                    # but a known stale lease must not crash the dispatcher.
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
            review_scheduler_checkpoint(
                self.audit_checkpoints,
                "integrity_alert",
                integrity_evidence(assignment, result, source="worker_completion"),
            )
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
                        {"job_id": job_id, "lease_id": assignment.lease.lease_id, "reason": "worker_acknowledged"},
                    )
        return True

    def reconcile(self, *, now: float | None = None) -> tuple[str, ...]:
        """Expire lost-worker leases and deterministically retry/fail their jobs."""

        with self._lock:
            current = self.clock() if now is None else now
            reconciled: list[str] = []
            for state in list(self._jobs.values()):
                lease = state.lease
                if state.status not in {"leased", "canceling"} or lease is None or lease.expires_at > current:
                    continue
                if state.status == "canceling":
                    self._record(
                        "job_canceled",
                        {"job_id": state.request.job_id, "lease_id": lease.lease_id, "reason": "cancel_lease_expired"},
                    )
                    reconciled.append(state.request.job_id)
                    continue
                event_type = "job_requeued" if state.attempts < state.request.max_attempts else "job_lease_failed"
                self._record(
                    event_type,
                    {"job_id": state.request.job_id, "lease_id": lease.lease_id, "reason": "lease_expired"},
                )
                reconciled.append(state.request.job_id)
        for job_id in reconciled:
            state = self._jobs[job_id]
            if state.status == "infrastructure_failed" and state.result is not None:
                review_scheduler_checkpoint(
                    self.audit_checkpoints,
                    "integrity_alert",
                    {
                        "campaign_id": state.request.campaign_id,
                        "job_id": job_id,
                        "branch_id": state.request.branch_id,
                        "source": "lease_reconciliation",
                        "result": result_to_dict(state.result),
                    },
                )
        return tuple(reconciled)

    def dispatch_once(self) -> int:
        """Claim available internal workers and execute one concurrent wave."""

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
        if not assignments:
            return 0

        def infrastructure_failure(exc: Exception) -> CampaignJobResult:
            return CampaignJobResult(
                outcome="infrastructure_failure",
                consumed=SchedulerBudget(wall_seconds=0.0),
                detail=f"{type(exc).__name__}: {exc}",
                cleanup_succeeded=False,
            )

        def execute_group(
            worker: CampaignWorker,
            grouped: tuple[CampaignAssignment, ...],
        ) -> tuple[CampaignJobResult, ...]:
            heartbeat_stop = threading.Event()
            heartbeat_thread = threading.Thread(
                target=self._heartbeat_assignments,
                args=(grouped, heartbeat_stop),
                daemon=True,
            )
            heartbeat_thread.start()
            try:
                execute_many = getattr(worker, "execute_many", None)
                if len(grouped) > 1 and callable(execute_many):
                    results = tuple(execute_many(grouped))
                    if len(results) != len(grouped):
                        raise RuntimeError("campaign batch worker returned the wrong number of results")
                    return results
                return tuple(worker.execute(assignment) for assignment in grouped)
            except Exception as exc:
                return tuple(infrastructure_failure(exc) for _ in grouped)
            finally:
                heartbeat_stop.set()
                heartbeat_thread.join(timeout=1.0)

        grouped_work: dict[tuple[str, str], tuple[CampaignWorker, list[CampaignAssignment]]] = {}
        for executor, assignment in zip(executors, assignments, strict=True):
            supports_batch = callable(getattr(executor, "execute_many", None))
            reusable = supports_batch and assignment.lease.lifecycle == "reuse_matched_trials"
            group_key = (
                assignment.lease.worker_id,
                assignment.lease.reuse_key if reusable else assignment.lease.lease_id,
            )
            if group_key not in grouped_work:
                grouped_work[group_key] = (executor, [])
            grouped_work[group_key][1].append(assignment)

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(grouped_work)) as pool:
            futures = {
                pool.submit(execute_group, executor, tuple(grouped)): tuple(grouped)
                for executor, grouped in grouped_work.values()
            }
            for future, grouped in futures.items():
                results = future.result()
                for assignment, result in zip(grouped, results, strict=True):
                    self.complete(assignment.lease.lease_id, result)
        return len(assignments)

    def serve(self, stop_event: threading.Event, *, poll_interval: float = 0.25) -> int:
        """Run a restart-safe live dispatch loop until ``stop_event`` is set."""

        if poll_interval <= 0:
            raise ValueError("campaign scheduler poll_interval must be positive")
        dispatched = 0
        try:
            while not stop_event.is_set():
                self.reconcile()
                count = self.dispatch_once()
                dispatched += count
                if count == 0:
                    stop_event.wait(poll_interval)
        finally:
            self._audit_final_summaries()
        return dispatched

    def run_until_idle(self, *, max_waves: int = 100) -> int:
        dispatched = 0
        for _ in range(max_waves):
            count = self.dispatch_once()
            if count == 0:
                break
            dispatched += count
        self._audit_final_summaries()
        return dispatched

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
                worker_utilization=utilization,
                events=self._sequence,
                audit_records_by_campaign=durable_audit_records(
                    self.audit_checkpoints,
                    tuple(self._campaign_ids()),
                ),
            )

    def _campaign_ids(self) -> set[str]:
        return set(self._campaign_limits) | {state.request.campaign_id for state in self._jobs.values()}

    def _audit_final_summaries(self) -> None:
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
            review_scheduler_checkpoint(self.audit_checkpoints, "final_completion", packet)

    def _record(self, event_type: str, payload: Mapping[str, Any]) -> None:
        # Keep sequence allocation, durable append, and in-memory application
        # inside one critical section.  Several public methods already hold the
        # re-entrant lock, but this boundary protects every caller by default.
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
            self.heartbeat(worker_id, lease_ids)

    def _apply(self, event: SchedulerEvent) -> None:
        self._sequence = event.sequence
        payload = event.payload
        if event.event_type == "campaign_configured":
            campaign_id = str(payload["campaign_id"])
            self._campaign_limits[campaign_id] = budget_from(payload["budget"])
            branch_budgets = mapping(payload.get("branch_budgets", {}))
            for branch, budget in branch_budgets.items():
                self._branch_limits[(campaign_id, branch)] = budget_from(budget)
            return
        if event.event_type == "worker_registered":
            descriptor = worker_from(payload["worker"])
            prior = self._workers.get(descriptor.worker_id)
            self._workers[descriptor.worker_id] = _WorkerState(
                descriptor=descriptor,
                last_heartbeat=as_float(payload["at"]),
                active_leases=set(prior.active_leases) if prior else set(),
                completed_jobs=prior.completed_jobs if prior else 0,
                failed_jobs=prior.failed_jobs if prior else 0,
                consumed=prior.consumed if prior else SchedulerBudget(),
            )
            return
        if event.event_type == "worker_heartbeat":
            worker = self._workers[str(payload["worker_id"])]
            worker.last_heartbeat = as_float(payload["at"])
            for lease_id in sequence(payload.get("lease_ids", [])):
                job_id = self._leases.get(str(lease_id))
                if job_id is None:
                    continue
                lease = self._jobs[job_id].lease
                if lease is not None:
                    self._jobs[job_id].lease = replace_lease_expiry(lease, as_float(payload["expires_at"]))
            return
        if event.event_type == "evidence_granted":
            grant = evidence_from(payload["grant"])
            self._evidence_grants[grant.grant_id] = grant
            self._campaign_consumed[grant.campaign_id] = self._campaign_consumed.get(grant.campaign_id, SchedulerBudget()).add(
                SchedulerBudget(shared_evidence_tokens=grant.token_cost)
            )
            return
        if event.event_type == "job_enqueued":
            request = job_from(payload["job"])
            if request.job_id in self._jobs:
                raise ValueError(f"duplicate campaign job_id in event log: {request.job_id}")
            idempotency_key = (request.campaign_id, request.idempotency_key)
            if idempotency_key in self._idempotency:
                raise ValueError("duplicate campaign idempotency key in event log")
            self._jobs[request.job_id] = _JobState(request=request)
            self._idempotency[idempotency_key] = request.job_id
            if request.cohort_id:
                self._cohort_lanes[(request.campaign_id, request.cohort_id)] = request.lane.digest
            return
        if event.event_type == "warm_degraded":
            return
        if event.event_type == "job_leased":
            lease = lease_from(payload["lease"])
            state = self._jobs[lease.job_id]
            state.status = "leased"
            state.attempts = lease.attempt
            state.lease = lease
            self._leases[lease.lease_id] = lease.job_id
            self._lease_history[lease.lease_id] = lease.job_id
            self._workers[lease.worker_id].active_leases.add(lease.lease_id)
            self._reserve(state.request)
            if state.request.cohort_id:
                self._cohort_environments[(state.request.campaign_id, state.request.cohort_id)] = lease.environment_fingerprint
            return
        if event.event_type == "job_budget_exhausted":
            self._jobs[str(payload["job_id"])].status = "budget_exhausted"
            return
        if event.event_type == "job_cancel_requested":
            self._jobs[str(payload["job_id"])].status = "canceling"
            return
        if event.event_type == "job_canceled_late":
            state = self._jobs[str(payload["job_id"])]
            result = result_from(payload["result"])
            lease_id = str(payload["lease_id"])
            worker_id = state.lease.worker_id if state.lease else ""
            self._release_lease(state)
            state.status = "canceled"
            state.late_result = result
            self._late_completion_leases.add(lease_id)
            self._consume(state.request, result.consumed)
            self._late_completions += 1
            if worker_id:
                worker = self._workers[worker_id]
                worker.failed_jobs += 1
                worker.consumed = worker.consumed.add(result.consumed)
            return
        if event.event_type in {"job_requeued", "job_lease_failed", "job_canceled"}:
            state = self._jobs[str(payload["job_id"])]
            worker_id = state.lease.worker_id if state.lease else ""
            self._release_lease(state)
            if event.event_type == "job_requeued":
                serialized_result = payload.get("result")
                if serialized_result is not None:
                    result = result_from(serialized_result)
                    self._consume(state.request, result.consumed)
                    if worker_id:
                        worker = self._workers[worker_id]
                        worker.failed_jobs += 1
                        worker.consumed = worker.consumed.add(result.consumed)
                state.status = "queued"
                state.last_infrastructure_error = str(payload.get("reason", ""))
                self._retry_count += 1
            elif event.event_type == "job_lease_failed":
                state.status = "infrastructure_failed"
                state.result = CampaignJobResult(
                    outcome="infrastructure_failure",
                    detail=str(payload.get("reason", "lease_expired")),
                    cleanup_succeeded=False,
                )
            else:
                state.status = "canceled"
            return
        if event.event_type == "job_finished":
            state = self._jobs[str(payload["job_id"])]
            result = result_from(payload["result"])
            worker_id = state.lease.worker_id if state.lease else ""
            self._release_lease(state)
            state.result = result
            status_by_outcome: dict[JobOutcome, JobStatus] = {
                "candidate_success": "succeeded",
                "candidate_failure": "candidate_failed",
                "infrastructure_failure": "infrastructure_failed",
            }
            state.status = status_by_outcome[result.outcome]
            self._consume(state.request, result.consumed)
            if worker_id:
                worker = self._workers[worker_id]
                worker.completed_jobs += int(result.outcome == "candidate_success")
                worker.failed_jobs += int(result.outcome != "candidate_success")
                worker.consumed = worker.consumed.add(result.consumed)

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
