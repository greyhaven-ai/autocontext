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
from typing import Any

from autocontext.execution.campaign_scheduler_adapters import (
    CallableCampaignWorker,
    RemoteCampaignWorker,
    campaign_result_from_remote,
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
    CampaignEvidenceGrant,
    CampaignJobRequest,
    CampaignJobResult,
    CampaignLease,
    CampaignSchedulerReport,
    CampaignWorker,
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
from autocontext.execution.campaign_scheduler_store import CampaignSchedulerEventStore


class CampaignScheduler:
    """Provider-neutral lease scheduler with deterministic event replay."""

    def __init__(
        self,
        store: CampaignSchedulerEventStore,
        *,
        lease_seconds: float = 30.0,
        max_concurrency: int = 8,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if lease_seconds <= 0 or max_concurrency < 1:
            raise ValueError("lease_seconds and max_concurrency must be positive")
        self.store = store
        self.lease_seconds = lease_seconds
        self.max_concurrency = max_concurrency
        self.clock = clock
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
            take = max(0, min(available_slots, limit if limit is not None else available_slots))
            assignments: list[CampaignAssignment] = []
            for state in self._jobs.values():
                if len(assignments) >= take:
                    break
                if state.status != "queued" or not self._worker_matches(worker, state.request):
                    continue
                if not self._budget_available(state.request):
                    self._record("job_budget_exhausted", {"job_id": state.request.job_id})
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
                completed = self._jobs[historical_job].result if historical_job is not None else None
                if completed is not None:
                    return completed
                raise ValueError("lease is stale or unknown")
            state = self._jobs[job_id]
            if state.status != "leased" or state.lease is None or state.lease.lease_id != lease_id:
                if state.result is not None:
                    return state.result
                raise ValueError("lease is no longer active")
            if result.outcome == "infrastructure_failure" and state.attempts < state.request.max_attempts:
                self._record(
                    "job_requeued",
                    {"job_id": job_id, "lease_id": lease_id, "reason": result.detail or "infrastructure_failure"},
                )
                return result
            self._record(
                "job_finished",
                {"job_id": job_id, "lease_id": lease_id, "result": result_to_dict(result)},
            )
            return result

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            state = self._jobs.get(job_id)
            if state is None or state.status in TERMINAL_STATUSES:
                return False
            self._record("job_canceled", {"job_id": job_id})
            return True

    def reconcile(self, *, now: float | None = None) -> tuple[str, ...]:
        """Expire lost-worker leases and deterministically retry/fail their jobs."""

        with self._lock:
            current = self.clock() if now is None else now
            reconciled: list[str] = []
            for state in list(self._jobs.values()):
                lease = state.lease
                if state.status != "leased" or lease is None or lease.expires_at > current:
                    continue
                event_type = "job_requeued" if state.attempts < state.request.max_attempts else "job_lease_failed"
                self._record(
                    event_type,
                    {"job_id": state.request.job_id, "lease_id": lease.lease_id, "reason": "lease_expired"},
                )
                reconciled.append(state.request.job_id)
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

        def execute_one(worker: CampaignWorker, assignment: CampaignAssignment) -> CampaignJobResult:
            try:
                return worker.execute(assignment)
            except Exception as exc:
                return CampaignJobResult(
                    outcome="infrastructure_failure",
                    consumed=SchedulerBudget(wall_seconds=0.0),
                    detail=f"{type(exc).__name__}: {exc}",
                    cleanup_succeeded=False,
                )

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(assignments)) as pool:
            futures = [
                pool.submit(execute_one, executor, assignment)
                for executor, assignment in zip(executors, assignments, strict=True)
            ]
            for assignment, future in zip(assignments, futures, strict=True):
                self.complete(assignment.lease.lease_id, future.result())
        return len(assignments)

    def run_until_idle(self, *, max_waves: int = 100) -> int:
        dispatched = 0
        for _ in range(max_waves):
            count = self.dispatch_once()
            if count == 0:
                break
            dispatched += count
        return dispatched

    def job_status(self, job_id: str) -> JobStatus:
        return self._jobs[job_id].status

    def job_result(self, job_id: str) -> CampaignJobResult | None:
        return self._jobs[job_id].result

    def report(self) -> CampaignSchedulerReport:
        counts = {status: 0 for status in TERMINAL_STATUSES | {"queued", "leased"}}
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
            running=counts["leased"],
            succeeded=counts["succeeded"],
            candidate_failed=counts["candidate_failed"],
            infrastructure_failed=counts["infrastructure_failed"],
            budget_exhausted=counts["budget_exhausted"],
            canceled=counts["canceled"],
            retries=self._retry_count,
            reserved_by_campaign=dict(self._campaign_reserved),
            consumed_by_campaign=dict(self._campaign_consumed),
            worker_utilization=utilization,
            events=self._sequence,
        )

    def _record(self, event_type: str, payload: Mapping[str, Any]) -> None:
        event = SchedulerEvent(
            sequence=self._sequence + 1,
            event_id=str(uuid.uuid4()),
            timestamp=self.clock(),
            event_type=event_type,
            payload=dict(payload),
        )
        self.store.append(event)
        self._apply(event)

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
            self._jobs[request.job_id] = _JobState(request=request)
            self._idempotency[(request.campaign_id, request.idempotency_key)] = request.job_id
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
        if event.event_type in {"job_requeued", "job_lease_failed", "job_canceled"}:
            state = self._jobs[str(payload["job_id"])]
            self._release_lease(state)
            if event.event_type == "job_requeued":
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

    def _budget_available(self, request: CampaignJobRequest) -> bool:
        campaign_limit = self._campaign_limits.get(request.campaign_id)
        if campaign_limit is not None:
            projected = (
                self._campaign_reserved.get(request.campaign_id, SchedulerBudget())
                .add(self._campaign_consumed.get(request.campaign_id, SchedulerBudget()))
                .add(request.reservation)
            )
            if not projected.within(campaign_limit):
                return False
        branch_key = (request.campaign_id, request.branch_id)
        branch_limit = self._branch_limits.get(branch_key)
        if branch_limit is not None:
            projected = (
                self._branch_reserved.get(branch_key, SchedulerBudget())
                .add(self._branch_consumed.get(branch_key, SchedulerBudget()))
                .add(request.reservation)
            )
            if not projected.within(branch_limit):
                return False
        return True

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
    "WorkerDescriptor",
    "campaign_result_from_remote",
]
