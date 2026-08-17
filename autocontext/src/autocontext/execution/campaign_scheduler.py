"""Optional resource-aware campaign scheduler (AC-979).

The scheduler coordinates user-controlled local, trusted-host, and remote
workers. Its append-only event log is the source of truth for leases, retries,
budgets, comparable evaluation cohorts, utilization, and restart recovery.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, TypeAlias

from autocontext.execution.remote_execution import (
    RemoteExecutionAdapter,
    RemoteExecutionRequest,
    RemoteExecutionResult,
)

JobStatus: TypeAlias = Literal[
    "queued",
    "leased",
    "succeeded",
    "candidate_failed",
    "infrastructure_failed",
    "budget_exhausted",
    "canceled",
]
JobOutcome: TypeAlias = Literal["candidate_success", "candidate_failure", "infrastructure_failure"]
AssignmentLifecycle: TypeAlias = Literal["ephemeral_per_eval", "reuse_matched_trials", "warm_snapshot"]


@dataclass(frozen=True, slots=True)
class SchedulerResources:
    cpu_cores: float = 1.0
    memory_gb: float = 1.0
    disk_gb: float = 1.0
    accelerator_kind: str | None = None
    accelerator_count: int = 0

    def __post_init__(self) -> None:
        if self.cpu_cores < 0 or self.memory_gb < 0 or self.disk_gb < 0 or self.accelerator_count < 0:
            raise ValueError("scheduler resources cannot be negative")
        if self.accelerator_count and not self.accelerator_kind:
            raise ValueError("accelerator_count requires accelerator_kind")

    def fits(self, required: SchedulerResources) -> bool:
        accelerator_fits = required.accelerator_count == 0 or (
            self.accelerator_kind == required.accelerator_kind
            and self.accelerator_count >= required.accelerator_count
        )
        return (
            self.cpu_cores >= required.cpu_cores
            and self.memory_gb >= required.memory_gb
            and self.disk_gb >= required.disk_gb
            and accelerator_fits
        )

    def add(self, other: SchedulerResources) -> SchedulerResources:
        kind = self.accelerator_kind or other.accelerator_kind
        if self.accelerator_count and other.accelerator_count and self.accelerator_kind != other.accelerator_kind:
            raise ValueError("cannot add unlike accelerator resources")
        return SchedulerResources(
            cpu_cores=self.cpu_cores + other.cpu_cores,
            memory_gb=self.memory_gb + other.memory_gb,
            disk_gb=self.disk_gb + other.disk_gb,
            accelerator_kind=kind,
            accelerator_count=self.accelerator_count + other.accelerator_count,
        )

    def subtract(self, other: SchedulerResources) -> SchedulerResources:
        return SchedulerResources(
            cpu_cores=max(0.0, self.cpu_cores - other.cpu_cores),
            memory_gb=max(0.0, self.memory_gb - other.memory_gb),
            disk_gb=max(0.0, self.disk_gb - other.disk_gb),
            accelerator_kind=self.accelerator_kind,
            accelerator_count=max(0, self.accelerator_count - other.accelerator_count),
        )


@dataclass(frozen=True, slots=True)
class SchedulerBudget:
    tokens: int = 0
    wall_seconds: float = 0.0
    compute_units: float = 0.0
    jobs: int = 0
    shared_evidence_tokens: int = 0

    def __post_init__(self) -> None:
        if min(self.tokens, self.wall_seconds, self.compute_units, self.jobs, self.shared_evidence_tokens) < 0:
            raise ValueError("scheduler budgets cannot be negative")

    def add(self, other: SchedulerBudget) -> SchedulerBudget:
        return SchedulerBudget(
            tokens=self.tokens + other.tokens,
            wall_seconds=self.wall_seconds + other.wall_seconds,
            compute_units=self.compute_units + other.compute_units,
            jobs=self.jobs + other.jobs,
            shared_evidence_tokens=self.shared_evidence_tokens + other.shared_evidence_tokens,
        )

    def subtract(self, other: SchedulerBudget) -> SchedulerBudget:
        return SchedulerBudget(
            tokens=max(0, self.tokens - other.tokens),
            wall_seconds=max(0.0, self.wall_seconds - other.wall_seconds),
            compute_units=max(0.0, self.compute_units - other.compute_units),
            jobs=max(0, self.jobs - other.jobs),
            shared_evidence_tokens=max(0, self.shared_evidence_tokens - other.shared_evidence_tokens),
        )

    def within(self, limit: SchedulerBudget) -> bool:
        return (
            (limit.tokens == 0 or self.tokens <= limit.tokens)
            and (limit.wall_seconds == 0 or self.wall_seconds <= limit.wall_seconds)
            and (limit.compute_units == 0 or self.compute_units <= limit.compute_units)
            and (limit.jobs == 0 or self.jobs <= limit.jobs)
            and (
                limit.shared_evidence_tokens == 0
                or self.shared_evidence_tokens <= limit.shared_evidence_tokens
            )
        )


@dataclass(frozen=True, slots=True)
class EvaluationLaneIdentity:
    lane_id: str
    fixture_digest: str
    seeds: tuple[str, ...]
    evaluator_epoch: str
    verifier_contract_ref: str

    def __post_init__(self) -> None:
        if not all(
            value.strip()
            for value in (self.lane_id, self.fixture_digest, self.evaluator_epoch, self.verifier_contract_ref)
        ):
            raise ValueError("evaluation lane identity fields must be non-empty")

    @property
    def digest(self) -> str:
        return _digest(asdict(self))


@dataclass(frozen=True, slots=True)
class WorkerDescriptor:
    worker_id: str
    runtime: str
    resources: SchedulerResources
    capabilities: frozenset[str] = frozenset()
    sandbox_features: frozenset[str] = frozenset()
    locality: str = "local"
    concurrency: int = 1
    environment_labels: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.worker_id.strip() or not self.runtime.strip() or self.concurrency < 1:
            raise ValueError("worker id/runtime must be non-empty and concurrency must be positive")

    @property
    def environment_fingerprint(self) -> str:
        return _digest(
            {
                "runtime": self.runtime,
                "resources": asdict(self.resources),
                "capabilities": sorted(self.capabilities),
                "sandbox_features": sorted(self.sandbox_features),
                "locality": self.locality,
                "environment_labels": dict(sorted(self.environment_labels.items())),
            }
        )


@dataclass(frozen=True, slots=True)
class CampaignEvidenceGrant:
    grant_id: str
    campaign_id: str
    from_branch_id: str
    to_branch_id: str
    evidence_ref: str
    token_cost: int


@dataclass(frozen=True, slots=True)
class CampaignJobRequest:
    job_id: str
    idempotency_key: str
    campaign_id: str
    branch_id: str
    job_kind: Literal["branch", "trial"]
    lane: EvaluationLaneIdentity
    resources: SchedulerResources = field(default_factory=SchedulerResources)
    required_capabilities: frozenset[str] = frozenset()
    reservation: SchedulerBudget = field(default_factory=lambda: SchedulerBudget(jobs=1))
    max_attempts: int = 2
    cohort_id: str = ""
    prefer_warm_reuse: bool = False
    evidence_grant_ids: tuple[str, ...] = ()
    payload: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.job_id, self.idempotency_key, self.campaign_id, self.branch_id)):
            raise ValueError("campaign job identity fields must be non-empty")
        if self.max_attempts < 1:
            raise ValueError("campaign job max_attempts must be positive")


@dataclass(frozen=True, slots=True)
class CampaignJobResult:
    outcome: JobOutcome
    consumed: SchedulerBudget = field(default_factory=lambda: SchedulerBudget(jobs=1))
    output_ref: str = ""
    detail: str = ""
    cleanup_succeeded: bool = True
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CampaignLease:
    lease_id: str
    job_id: str
    worker_id: str
    attempt: int
    issued_at: float
    expires_at: float
    environment_fingerprint: str
    lifecycle: AssignmentLifecycle
    reuse_key: str


@dataclass(frozen=True, slots=True)
class CampaignAssignment:
    job: CampaignJobRequest
    lease: CampaignLease


class CampaignWorker(Protocol):
    def execute(self, assignment: CampaignAssignment) -> CampaignJobResult:
        """Execute one leased campaign assignment."""
        ...


class CallableCampaignWorker:
    def __init__(self, execute: Callable[[CampaignAssignment], CampaignJobResult]) -> None:
        self._execute = execute

    def execute(self, assignment: CampaignAssignment) -> CampaignJobResult:
        return self._execute(assignment)


class RemoteCampaignWorker:
    """Map a remote execution adapter into the scheduler worker contract."""

    def __init__(
        self,
        adapter: RemoteExecutionAdapter,
        request_factory: Callable[[CampaignAssignment], RemoteExecutionRequest],
    ) -> None:
        self._adapter = adapter
        self._request_factory = request_factory

    def execute(self, assignment: CampaignAssignment) -> CampaignJobResult:
        result = self._adapter.execute_request(self._request_factory(assignment))
        return campaign_result_from_remote(result)


@dataclass(frozen=True, slots=True)
class SchedulerEvent:
    sequence: int
    event_id: str
    timestamp: float
    event_type: str
    payload: Mapping[str, object]


class CampaignSchedulerEventStore:
    """Checksummed append-only JSONL event store with fsync durability."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def append(self, event: SchedulerEvent) -> None:
        body = {
            "sequence": event.sequence,
            "event_id": event.event_id,
            "timestamp": event.timestamp,
            "event_type": event.event_type,
            "payload": event.payload,
        }
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":"))
        line = json.dumps({**body, "checksum": hashlib.sha256(encoded.encode()).hexdigest()}, sort_keys=True) + "\n"
        with self._lock:
            descriptor = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
            try:
                os.write(descriptor, line.encode())
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def read(self) -> tuple[SchedulerEvent, ...]:
        if not self.path.exists():
            return ()
        events: list[SchedulerEvent] = []
        for line_number, line in enumerate(self.path.read_text().splitlines(), start=1):
            try:
                data = json.loads(line)
                checksum = str(data.pop("checksum"))
                encoded = json.dumps(data, sort_keys=True, separators=(",", ":"))
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                raise ValueError(f"invalid scheduler event at line {line_number}") from exc
            if hashlib.sha256(encoded.encode()).hexdigest() != checksum:
                raise ValueError(f"scheduler event checksum mismatch at line {line_number}")
            expected = len(events) + 1
            if data.get("sequence") != expected:
                raise ValueError(f"scheduler event sequence mismatch at line {line_number}")
            events.append(
                SchedulerEvent(
                    sequence=expected,
                    event_id=str(data["event_id"]),
                    timestamp=float(data["timestamp"]),
                    event_type=str(data["event_type"]),
                    payload=dict(data["payload"]),
                )
            )
        return tuple(events)


@dataclass(slots=True)
class _JobState:
    request: CampaignJobRequest
    status: JobStatus = "queued"
    attempts: int = 0
    lease: CampaignLease | None = None
    result: CampaignJobResult | None = None
    last_infrastructure_error: str = ""


@dataclass(slots=True)
class _WorkerState:
    descriptor: WorkerDescriptor
    last_heartbeat: float
    active_leases: set[str] = field(default_factory=set)
    completed_jobs: int = 0
    failed_jobs: int = 0
    consumed: SchedulerBudget = field(default_factory=SchedulerBudget)


@dataclass(frozen=True, slots=True)
class CampaignSchedulerReport:
    queued: int
    running: int
    succeeded: int
    candidate_failed: int
    infrastructure_failed: int
    budget_exhausted: int
    canceled: int
    retries: int
    reserved_by_campaign: Mapping[str, SchedulerBudget]
    consumed_by_campaign: Mapping[str, SchedulerBudget]
    worker_utilization: Mapping[str, Mapping[str, object]]
    events: int


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
                self._record("worker_registered", {"worker": _worker_to_dict(descriptor), "at": self.clock()})
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
            self._record("job_enqueued", {"job": _job_to_dict(request)})
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
                    reuse_key=_reuse_key(state.request),
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
                {"job_id": job_id, "lease_id": lease_id, "result": _result_to_dict(result)},
            )
            return result

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            state = self._jobs.get(job_id)
            if state is None or state.status in _TERMINAL_STATUSES:
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
            global_available = self.max_concurrency - sum(
                len(worker.active_leases) for worker in self._workers.values()
            )
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
        counts = {status: 0 for status in _TERMINAL_STATUSES | {"queued", "leased"}}
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

    def _record(self, event_type: str, payload: Mapping[str, object]) -> None:
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
            self._campaign_limits[campaign_id] = _budget_from(payload["budget"])
            branch_budgets = _mapping(payload.get("branch_budgets", {}))
            for branch, budget in branch_budgets.items():
                self._branch_limits[(campaign_id, branch)] = _budget_from(budget)
            return
        if event.event_type == "worker_registered":
            descriptor = _worker_from(payload["worker"])
            prior = self._workers.get(descriptor.worker_id)
            self._workers[descriptor.worker_id] = _WorkerState(
                descriptor=descriptor,
                last_heartbeat=_float(payload["at"]),
                active_leases=set(prior.active_leases) if prior else set(),
                completed_jobs=prior.completed_jobs if prior else 0,
                failed_jobs=prior.failed_jobs if prior else 0,
                consumed=prior.consumed if prior else SchedulerBudget(),
            )
            return
        if event.event_type == "worker_heartbeat":
            worker = self._workers[str(payload["worker_id"])]
            worker.last_heartbeat = _float(payload["at"])
            for lease_id in _sequence(payload.get("lease_ids", [])):
                job_id = self._leases.get(str(lease_id))
                if job_id is None:
                    continue
                lease = self._jobs[job_id].lease
                if lease is not None:
                    self._jobs[job_id].lease = replace_lease_expiry(lease, _float(payload["expires_at"]))
            return
        if event.event_type == "evidence_granted":
            grant = _evidence_from(payload["grant"])
            self._evidence_grants[grant.grant_id] = grant
            self._campaign_consumed[grant.campaign_id] = self._campaign_consumed.get(
                grant.campaign_id, SchedulerBudget()
            ).add(SchedulerBudget(shared_evidence_tokens=grant.token_cost))
            return
        if event.event_type == "job_enqueued":
            request = _job_from(payload["job"])
            self._jobs[request.job_id] = _JobState(request=request)
            self._idempotency[(request.campaign_id, request.idempotency_key)] = request.job_id
            if request.cohort_id:
                self._cohort_lanes[(request.campaign_id, request.cohort_id)] = request.lane.digest
            return
        if event.event_type == "warm_degraded":
            return
        if event.event_type == "job_leased":
            lease = _lease_from(payload["lease"])
            state = self._jobs[lease.job_id]
            state.status = "leased"
            state.attempts = lease.attempt
            state.lease = lease
            self._leases[lease.lease_id] = lease.job_id
            self._lease_history[lease.lease_id] = lease.job_id
            self._workers[lease.worker_id].active_leases.add(lease.lease_id)
            self._reserve(state.request)
            if state.request.cohort_id:
                self._cohort_environments[(state.request.campaign_id, state.request.cohort_id)] = (
                    lease.environment_fingerprint
                )
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
            result = _result_from(payload["result"])
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
            projected = self._campaign_reserved.get(request.campaign_id, SchedulerBudget()).add(
                self._campaign_consumed.get(request.campaign_id, SchedulerBudget())
            ).add(request.reservation)
            if not projected.within(campaign_limit):
                return False
        branch_key = (request.campaign_id, request.branch_id)
        branch_limit = self._branch_limits.get(branch_key)
        if branch_limit is not None:
            projected = self._branch_reserved.get(branch_key, SchedulerBudget()).add(
                self._branch_consumed.get(branch_key, SchedulerBudget())
            ).add(request.reservation)
            if not projected.within(branch_limit):
                return False
        return True

    def _reserve(self, request: CampaignJobRequest) -> None:
        campaign = request.campaign_id
        branch = (request.campaign_id, request.branch_id)
        self._campaign_reserved[campaign] = self._campaign_reserved.get(campaign, SchedulerBudget()).add(
            request.reservation
        )
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
        self._campaign_reserved[campaign] = self._campaign_reserved.get(campaign, SchedulerBudget()).subtract(
            request.reservation
        )
        self._branch_reserved[branch] = self._branch_reserved.get(branch, SchedulerBudget()).subtract(
            request.reservation
        )

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


def campaign_result_from_remote(result: RemoteExecutionResult) -> CampaignJobResult:
    if result.status == "success":
        outcome: JobOutcome = "candidate_success"
    elif result.status in {"task_error", "artifact_error"}:
        outcome = "candidate_failure"
    else:
        outcome = "infrastructure_failure"
    return CampaignJobResult(
        outcome=outcome,
        consumed=SchedulerBudget(
            wall_seconds=result.usage.wall_seconds,
            compute_units=result.usage.accelerator_seconds or result.usage.cpu_seconds or 0.0,
            jobs=1,
        ),
        detail=result.error,
        cleanup_succeeded=result.cleanup.succeeded,
        metadata={"remote_status": result.status, "provider": result.provider, "session_id": result.session_id},
    )


def replace_lease_expiry(lease: CampaignLease, expires_at: float) -> CampaignLease:
    return CampaignLease(
        lease_id=lease.lease_id,
        job_id=lease.job_id,
        worker_id=lease.worker_id,
        attempt=lease.attempt,
        issued_at=lease.issued_at,
        expires_at=expires_at,
        environment_fingerprint=lease.environment_fingerprint,
        lifecycle=lease.lifecycle,
        reuse_key=lease.reuse_key,
    )


def _reuse_key(request: CampaignJobRequest) -> str:
    scope = request.cohort_id or request.job_id
    return f"{request.campaign_id}:{request.branch_id}:{scope}"


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("expected a mapping")
    return {str(key): item for key, item in value.items()}


def _sequence(value: object) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise TypeError("expected a sequence")
    return value


def _float(value: object) -> float:
    if not isinstance(value, (str, int, float)):
        raise TypeError("expected a number")
    return float(value)


def _budget_from(value: object) -> SchedulerBudget:
    return SchedulerBudget(**_mapping(value))


def _resources_from(value: object) -> SchedulerResources:
    return SchedulerResources(**_mapping(value))


def _lane_from(value: object) -> EvaluationLaneIdentity:
    data = _mapping(value)
    return EvaluationLaneIdentity(
        lane_id=str(data["lane_id"]),
        fixture_digest=str(data["fixture_digest"]),
        seeds=tuple(str(seed) for seed in data["seeds"]),
        evaluator_epoch=str(data["evaluator_epoch"]),
        verifier_contract_ref=str(data["verifier_contract_ref"]),
    )


def _worker_to_dict(worker: WorkerDescriptor) -> dict[str, object]:
    return {
        **asdict(worker),
        "capabilities": sorted(worker.capabilities),
        "sandbox_features": sorted(worker.sandbox_features),
    }


def _worker_from(value: object) -> WorkerDescriptor:
    data = _mapping(value)
    return WorkerDescriptor(
        worker_id=str(data["worker_id"]),
        runtime=str(data["runtime"]),
        resources=_resources_from(data["resources"]),
        capabilities=frozenset(str(item) for item in data.get("capabilities", [])),
        sandbox_features=frozenset(str(item) for item in data.get("sandbox_features", [])),
        locality=str(data.get("locality", "local")),
        concurrency=int(data.get("concurrency", 1)),
        environment_labels={str(key): str(item) for key, item in _mapping(data.get("environment_labels", {})).items()},
    )


def _job_to_dict(job: CampaignJobRequest) -> dict[str, object]:
    return {
        **asdict(job),
        "required_capabilities": sorted(job.required_capabilities),
        "lane": asdict(job.lane),
    }


def _job_from(value: object) -> CampaignJobRequest:
    data = _mapping(value)
    return CampaignJobRequest(
        job_id=str(data["job_id"]),
        idempotency_key=str(data["idempotency_key"]),
        campaign_id=str(data["campaign_id"]),
        branch_id=str(data["branch_id"]),
        job_kind=str(data["job_kind"]),  # type: ignore[arg-type]
        lane=_lane_from(data["lane"]),
        resources=_resources_from(data["resources"]),
        required_capabilities=frozenset(str(item) for item in data.get("required_capabilities", [])),
        reservation=_budget_from(data["reservation"]),
        max_attempts=int(data["max_attempts"]),
        cohort_id=str(data.get("cohort_id", "")),
        prefer_warm_reuse=bool(data.get("prefer_warm_reuse", False)),
        evidence_grant_ids=tuple(str(item) for item in data.get("evidence_grant_ids", [])),
        payload=_mapping(data.get("payload", {})),
    )


def _lease_from(value: object) -> CampaignLease:
    data = _mapping(value)
    return CampaignLease(
        lease_id=str(data["lease_id"]),
        job_id=str(data["job_id"]),
        worker_id=str(data["worker_id"]),
        attempt=int(data["attempt"]),
        issued_at=float(data["issued_at"]),
        expires_at=float(data["expires_at"]),
        environment_fingerprint=str(data["environment_fingerprint"]),
        lifecycle=str(data["lifecycle"]),  # type: ignore[arg-type]
        reuse_key=str(data["reuse_key"]),
    )


def _result_to_dict(result: CampaignJobResult) -> dict[str, object]:
    return {**asdict(result), "consumed": asdict(result.consumed)}


def _result_from(value: object) -> CampaignJobResult:
    data = _mapping(value)
    return CampaignJobResult(
        outcome=str(data["outcome"]),  # type: ignore[arg-type]
        consumed=_budget_from(data["consumed"]),
        output_ref=str(data.get("output_ref", "")),
        detail=str(data.get("detail", "")),
        cleanup_succeeded=bool(data.get("cleanup_succeeded", True)),
        metadata=_mapping(data.get("metadata", {})),
    )


def _evidence_from(value: object) -> CampaignEvidenceGrant:
    data = _mapping(value)
    return CampaignEvidenceGrant(
        grant_id=str(data["grant_id"]),
        campaign_id=str(data["campaign_id"]),
        from_branch_id=str(data["from_branch_id"]),
        to_branch_id=str(data["to_branch_id"]),
        evidence_ref=str(data["evidence_ref"]),
        token_cost=int(data["token_cost"]),
    )


_TERMINAL_STATUSES: frozenset[JobStatus] = frozenset(
    {"succeeded", "candidate_failed", "infrastructure_failed", "budget_exhausted", "canceled"}
)


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
