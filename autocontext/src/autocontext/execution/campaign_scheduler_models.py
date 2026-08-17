"""Data, worker adapters, and durable codecs for the campaign scheduler."""

from __future__ import annotations

import hashlib
import json
import os
import threading
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
        values = (self.lane_id, self.fixture_digest, self.evaluator_epoch, self.verifier_contract_ref)
        if not all(value.strip() for value in values):
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
        identities = (self.job_id, self.idempotency_key, self.campaign_id, self.branch_id)
        if not all(value.strip() for value in identities):
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
    def execute(self, assignment: CampaignAssignment) -> CampaignJobResult: ...


class CallableCampaignWorker:
    def __init__(self, execute: Callable[[CampaignAssignment], CampaignJobResult]) -> None:
        self._execute = execute

    def execute(self, assignment: CampaignAssignment) -> CampaignJobResult:
        return self._execute(assignment)


class RemoteCampaignWorker:
    def __init__(
        self,
        adapter: RemoteExecutionAdapter,
        request_factory: Callable[[CampaignAssignment], RemoteExecutionRequest],
    ) -> None:
        self._adapter = adapter
        self._request_factory = request_factory

    def execute(self, assignment: CampaignAssignment) -> CampaignJobResult:
        return campaign_result_from_remote(self._adapter.execute_request(self._request_factory(assignment)))


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


def _worker_to_dict(worker: WorkerDescriptor) -> dict[str, Any]:
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


def _job_to_dict(job: CampaignJobRequest) -> dict[str, Any]:
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


def _result_to_dict(result: CampaignJobResult) -> dict[str, Any]:
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
