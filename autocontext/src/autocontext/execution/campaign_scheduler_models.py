"""Domain models and state for the resource-aware campaign scheduler."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Literal, Protocol, TypeAlias

from autocontext.context_bundles.models import stable_digest
from autocontext.execution.remote_execution import RemoteLifecyclePolicy

JobStatus: TypeAlias = Literal[
    "queued",
    "leased",
    "canceling",
    "succeeded",
    "candidate_failed",
    "infrastructure_failed",
    "budget_exhausted",
    "canceled",
]
JobOutcome: TypeAlias = Literal["candidate_success", "candidate_failure", "infrastructure_failure"]
AssignmentLifecycle: TypeAlias = RemoteLifecyclePolicy


@dataclass(frozen=True, slots=True)
class SchedulerResources:
    cpu_cores: float = 1.0
    memory_gb: float = 1.0
    disk_gb: float = 1.0
    accelerator_kind: str | None = None
    accelerator_count: int = 0

    def __post_init__(self) -> None:
        for field_name, value in (
            ("cpu_cores", self.cpu_cores),
            ("memory_gb", self.memory_gb),
            ("disk_gb", self.disk_gb),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise TypeError(f"scheduler resource {field_name} must be a finite number")
        if isinstance(self.accelerator_count, bool) or not isinstance(self.accelerator_count, int):
            raise TypeError("scheduler resource accelerator_count must be an integer")
        if self.accelerator_kind is not None and (
            not isinstance(self.accelerator_kind, str) or not self.accelerator_kind.strip()
        ):
            raise TypeError("scheduler resource accelerator_kind must be a non-empty string when provided")
        if self.cpu_cores < 0 or self.memory_gb < 0 or self.disk_gb < 0 or self.accelerator_count < 0:
            raise ValueError("scheduler resources cannot be negative")
        if self.accelerator_count and not self.accelerator_kind:
            raise ValueError("accelerator_count requires accelerator_kind")

    def fits(self, required: SchedulerResources) -> bool:
        accelerator_fits = required.accelerator_count == 0 or (
            self.accelerator_kind == required.accelerator_kind and self.accelerator_count >= required.accelerator_count
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
        for field_name, value in (
            ("tokens", self.tokens),
            ("jobs", self.jobs),
            ("shared_evidence_tokens", self.shared_evidence_tokens),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"scheduler budget {field_name} must be an integer")
        for field_name, numeric_value in (
            ("wall_seconds", self.wall_seconds),
            ("compute_units", self.compute_units),
        ):
            if (
                isinstance(numeric_value, bool)
                or not isinstance(numeric_value, (int, float))
                or not math.isfinite(numeric_value)
            ):
                raise TypeError(f"scheduler budget {field_name} must be a finite number")
        if self.tokens < 0 or self.wall_seconds < 0 or self.compute_units < 0 or self.jobs < 0 or self.shared_evidence_tokens < 0:
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
            and (limit.shared_evidence_tokens == 0 or self.shared_evidence_tokens <= limit.shared_evidence_tokens)
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
        return stable_digest(asdict(self))


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
        if isinstance(self.concurrency, bool) or not isinstance(self.concurrency, int):
            raise TypeError("worker concurrency must be an integer")
        if not isinstance(self.worker_id, str) or not isinstance(self.runtime, str):
            raise TypeError("worker id/runtime must be strings")
        if not self.worker_id.strip() or not self.runtime.strip() or self.concurrency < 1:
            raise ValueError("worker id/runtime must be non-empty and concurrency must be positive")

    @property
    def environment_fingerprint(self) -> str:
        return stable_digest(
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

    def __post_init__(self) -> None:
        if isinstance(self.token_cost, bool) or not isinstance(self.token_cost, int):
            raise TypeError("campaign evidence token_cost must be an integer")
        if self.token_cost < 0:
            raise ValueError("campaign evidence token_cost cannot be negative")


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
        if isinstance(self.max_attempts, bool) or not isinstance(self.max_attempts, int):
            raise TypeError("campaign job max_attempts must be an integer")
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

    def __post_init__(self) -> None:
        if self.outcome not in {"candidate_success", "candidate_failure", "infrastructure_failure"}:
            raise ValueError(f"unsupported campaign job outcome: {self.outcome}")
        if not isinstance(self.consumed, SchedulerBudget):
            raise TypeError("campaign result consumed usage must be SchedulerBudget")
        if not isinstance(self.output_ref, str) or not isinstance(self.detail, str):
            raise TypeError("campaign result output_ref and detail must be strings")
        if not isinstance(self.cleanup_succeeded, bool):
            raise TypeError("campaign result cleanup_succeeded must be boolean")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("campaign result metadata must be a mapping")


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


class CampaignBatchWorker(CampaignWorker, Protocol):
    def execute_many(self, assignments: tuple[CampaignAssignment, ...]) -> tuple[CampaignJobResult, ...]: ...


class CancellableCampaignWorker(CampaignWorker, Protocol):
    def cancel(self, assignment: CampaignAssignment) -> bool: ...


@dataclass(frozen=True, slots=True)
class SchedulerEvent:
    sequence: int
    event_id: str
    timestamp: float
    event_type: str
    payload: Mapping[str, object]


@dataclass(slots=True)
class _JobState:
    request: CampaignJobRequest
    status: JobStatus = "queued"
    attempts: int = 0
    lease: CampaignLease | None = None
    result: CampaignJobResult | None = None
    accounting_result: CampaignJobResult | None = None
    late_result: CampaignJobResult | None = None
    last_infrastructure_error: str = ""


@dataclass(slots=True)
class _WorkerState:
    descriptor: WorkerDescriptor
    last_heartbeat: float
    active_leases: set[str] = field(default_factory=set)
    completed_jobs: int = 0
    failed_jobs: int = 0
    cleanup_succeeded: int = 0
    cleanup_failed: int = 0
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
    late_completions: int
    retries: int
    reserved_by_campaign: Mapping[str, SchedulerBudget]
    consumed_by_campaign: Mapping[str, SchedulerBudget]
    consumed_by_branch: Mapping[str, Mapping[str, SchedulerBudget]]
    cleanup_by_campaign: Mapping[str, Mapping[str, int]]
    worker_utilization: Mapping[str, Mapping[str, object]]
    events: int
    audit_records_by_campaign: Mapping[str, tuple[Mapping[str, object], ...]] = field(default_factory=dict)
    audit_failures_by_campaign: Mapping[str, tuple[Mapping[str, str], ...]] = field(default_factory=dict)


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


def reuse_key(request: CampaignJobRequest) -> str:
    scope = request.cohort_id or request.job_id
    return f"{request.campaign_id}:{request.branch_id}:{scope}"


TERMINAL_STATUSES: frozenset[JobStatus] = frozenset(
    {"succeeded", "candidate_failed", "infrastructure_failed", "budget_exhausted", "canceled"}
)


def finite_number(value: object, *, minimum: float, inclusive: bool) -> bool:
    """Return whether ``value`` is a non-boolean finite number within a bound."""

    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return False
    return value >= minimum if inclusive else value > minimum
