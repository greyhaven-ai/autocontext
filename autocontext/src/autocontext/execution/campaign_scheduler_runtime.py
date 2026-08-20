"""Production composition boundary for a ready campaign scheduler service."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field

from autocontext.context_bundles.models import stable_digest
from autocontext.execution.campaign_scheduler import CampaignScheduler
from autocontext.execution.campaign_scheduler_audits import SchedulerAuditRunner
from autocontext.execution.campaign_scheduler_codecs import job_from, job_to_dict
from autocontext.execution.campaign_scheduler_models import (
    CampaignJobRequest,
    CampaignWorker,
    SchedulerBudget,
    WorkerDescriptor,
)
from autocontext.execution.campaign_scheduler_store import CampaignSchedulerEventStore


@dataclass(frozen=True, slots=True)
class CampaignSchedulerRuntimePlan:
    campaign_id: str
    budget: SchedulerBudget
    jobs: tuple[CampaignJobRequest, ...]
    branch_budgets: Mapping[str, SchedulerBudget] = field(default_factory=dict)
    lease_seconds: float = 30.0
    max_concurrency: int = 8
    identity: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.campaign_id.strip():
            raise ValueError("campaign_id must be non-empty")
        if any(job.campaign_id != self.campaign_id for job in self.jobs):
            raise ValueError("every scheduler job must belong to the runtime plan campaign")
        if len({job.job_id for job in self.jobs}) != len(self.jobs):
            raise ValueError("scheduler runtime plan job_id values must be unique")
        if len({job.idempotency_key for job in self.jobs}) != len(self.jobs):
            raise ValueError("scheduler runtime plan idempotency keys must be unique")
        if any(not key.strip() or not value.strip() for key, value in self.identity.items()):
            raise ValueError("campaign scheduler runtime identity fields must be non-empty")


@dataclass(frozen=True, slots=True)
class CampaignWorkerBinding:
    descriptor: WorkerDescriptor
    executor: CampaignWorker


def build_campaign_scheduler_runtime(
    plan: CampaignSchedulerRuntimePlan,
    *,
    store: CampaignSchedulerEventStore,
    workers: Sequence[CampaignWorkerBinding],
    clock: Callable[[], float] = time.time,
    audit_checkpoints: SchedulerAuditRunner | None = None,
) -> CampaignScheduler:
    """Build or resume a configured scheduler with concrete worker bindings."""

    if not workers:
        raise ValueError("campaign scheduler runtime requires at least one worker")
    scheduler = CampaignScheduler(
        store,
        lease_seconds=plan.lease_seconds,
        max_concurrency=plan.max_concurrency,
        clock=clock,
        audit_checkpoints=audit_checkpoints,
    )
    _configure_once(scheduler, store, plan)
    for binding in workers:
        scheduler.register_worker(binding.descriptor, binding.executor)
    for job in plan.jobs:
        scheduler.enqueue(job)
    return scheduler


def _configure_once(
    scheduler: CampaignScheduler,
    store: CampaignSchedulerEventStore,
    plan: CampaignSchedulerRuntimePlan,
) -> None:
    expected_budget = asdict(plan.budget)
    expected_branches = {branch: asdict(budget) for branch, budget in plan.branch_budgets.items()}
    prior = [
        event.payload
        for event in store.read()
        if event.event_type == "campaign_configured" and event.payload.get("campaign_id") == plan.campaign_id
    ]
    if not prior:
        scheduler.configure_campaign(plan.campaign_id, plan.budget, branch_budgets=plan.branch_budgets)
    else:
        latest = prior[-1]
        if latest.get("budget") != expected_budget or latest.get("branch_budgets", {}) != expected_branches:
            raise ValueError("campaign scheduler runtime plan conflicts with durable campaign configuration")
    _validate_legacy_job_state(store, plan)
    scheduler.bind_runtime_plan(plan.campaign_id, _runtime_plan_fingerprint(plan))


def _runtime_plan_fingerprint(plan: CampaignSchedulerRuntimePlan) -> str:
    return stable_digest(
        {
            "campaign_id": plan.campaign_id,
            "budget": asdict(plan.budget),
            "branch_budgets": {branch: asdict(budget) for branch, budget in plan.branch_budgets.items()},
            "jobs": [job_to_dict(job) for job in plan.jobs],
            "lease_seconds": plan.lease_seconds,
            "max_concurrency": plan.max_concurrency,
            "identity": dict(plan.identity),
        }
    )


def _validate_legacy_job_state(store: CampaignSchedulerEventStore, plan: CampaignSchedulerRuntimePlan) -> None:
    """Fail closed when upgrading an unbound event log with different jobs."""

    events = store.read()
    if any(event.event_type == "runtime_plan_bound" and event.payload.get("campaign_id") == plan.campaign_id for event in events):
        return
    durable_jobs: list[CampaignJobRequest] = []
    for event in events:
        if event.event_type != "job_enqueued":
            continue
        durable_job = job_from(event.payload["job"])
        if durable_job.campaign_id == plan.campaign_id:
            durable_jobs.append(durable_job)
    durable = tuple(durable_jobs)
    if durable and durable != plan.jobs[: len(durable)]:
        raise ValueError("campaign scheduler runtime plan conflicts with legacy durable job state")


__all__ = [
    "CampaignSchedulerRuntimePlan",
    "CampaignWorkerBinding",
    "build_campaign_scheduler_runtime",
]
