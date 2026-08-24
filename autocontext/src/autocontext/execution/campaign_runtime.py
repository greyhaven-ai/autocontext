"""Shipped campaign-plan runtime backed by :class:`CampaignScheduler`.

The scheduler is useful only when a real entrypoint converts scenario trials
into durable jobs.  This module provides that production composition without
coupling the scheduler core to CLI or server concerns.
"""

from __future__ import annotations

import math
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from autocontext.analytics.campaign_mode_report import (
    CampaignModeReport,
    CampaignTerminalState,
    build_campaign_mode_report,
)
from autocontext.audit import CampaignAuditPacketIdentity
from autocontext.campaign_audit_runtime import build_live_campaign_auditor
from autocontext.config.settings import AppSettings
from autocontext.context_bundles.assembly import evaluator_epoch_for
from autocontext.context_bundles.models import stable_digest
from autocontext.context_bundles.runtime_evaluator import materialize_runtime_fixture
from autocontext.execution import campaign_remote as _campaign_remote
from autocontext.execution.campaign_scheduler import CallableCampaignWorker, CampaignScheduler
from autocontext.execution.campaign_scheduler_models import (
    CampaignAssignment,
    CampaignJobRequest,
    CampaignJobResult,
    EvaluationLaneIdentity,
    SchedulerBudget,
    WorkerDescriptor,
)
from autocontext.execution.campaign_scheduler_runtime import (
    CampaignSchedulerRuntimePlan,
    CampaignWorkerBinding,
    build_campaign_scheduler_runtime,
)
from autocontext.execution.campaign_scheduler_store import CampaignSchedulerEventStore
from autocontext.execution.remote_execution import (
    RemoteAcceleratorRequest,
    RemoteExecutionRequirements,
    RemoteExecutionResult,
    RemoteResourceRequest,
)
from autocontext.execution.runtime_factory import ExecutionRuntime, build_execution_runtime
from autocontext.execution.supervisor import ExecutionInput, ExecutionOutput
from autocontext.runtime_images import require_pinned_runtime_image
from autocontext.scenarios import resolve_scenario_class
from autocontext.scenarios.base import ExecutionLimits, ScenarioInterface
from autocontext.storage.campaign_mode_report_store import write_campaign_mode_report
from autocontext.util.json_io import read_json, write_json
from autocontext.util.models import StrictModel

CampaignPlanAccelerator = _campaign_remote.CampaignPlanAccelerator
CampaignPlanRemoteRequirements = _campaign_remote.CampaignPlanRemoteRequirements


class CampaignPlanBudget(StrictModel):
    tokens: int = Field(default=0, ge=0)
    wall_seconds: float = Field(default=0.0, ge=0.0, allow_inf_nan=False)
    compute_units: float = Field(default=0.0, ge=0.0, allow_inf_nan=False)
    jobs: int = Field(default=0, ge=0)
    shared_evidence_tokens: int = Field(default=0, ge=0)

    def scheduler_budget(self) -> SchedulerBudget:
        return SchedulerBudget(**self.to_dict())


class CampaignPlanJob(StrictModel):
    job_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    branch_id: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    job_kind: Literal["branch", "trial"] = "trial"
    strategy: dict[str, Any]
    seed: int
    lane_id: str = Field(min_length=1)
    fixture_digest: str = Field(min_length=1)
    evaluator_epoch: str = Field(min_length=1)
    verifier_contract_ref: str = Field(min_length=1)
    cohort_id: str = ""
    max_attempts: int = Field(default=2, ge=1)
    prefer_warm_reuse: bool = False
    timeout_seconds: float = Field(default=30.0, gt=0.0, allow_inf_nan=False)
    max_memory_mb: int = Field(default=512, ge=16)
    network_access: bool = False
    remote: CampaignPlanRemoteRequirements | None = None
    reservation: CampaignPlanBudget = Field(default_factory=lambda: CampaignPlanBudget(jobs=1))


class CampaignPlan(StrictModel):
    schema_version: Literal[1] = 1
    campaign_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    scenario_name: str = Field(min_length=1)
    budget: CampaignPlanBudget
    branch_budgets: dict[str, CampaignPlanBudget] = Field(default_factory=dict)
    jobs: list[CampaignPlanJob] = Field(min_length=1)
    max_concurrency: int = Field(default=1, ge=1)
    lease_seconds: float = Field(default=30.0, gt=0.0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _validate_job_identities(self) -> CampaignPlan:
        job_ids = [job.job_id for job in self.jobs]
        idempotency_keys = [job.idempotency_key for job in self.jobs]
        if len(set(job_ids)) != len(job_ids):
            raise ValueError("campaign plan job_id values must be unique")
        if len(set(idempotency_keys)) != len(idempotency_keys):
            raise ValueError("campaign plan idempotency_key values must be unique")
        branch_objectives: dict[str, str] = {}
        lane_contracts: dict[str, tuple[str, str]] = {}
        cohort_lanes: dict[str, tuple[str, str, int, str, str, str]] = {}
        for job in self.jobs:
            prior_objective = branch_objectives.setdefault(job.branch_id, job.objective)
            if prior_objective != job.objective:
                raise ValueError("campaign jobs in one branch must use the same objective")
            contract = (job.evaluator_epoch, job.verifier_contract_ref)
            prior_contract = lane_contracts.setdefault(job.lane_id, contract)
            if prior_contract != contract:
                raise ValueError("campaign jobs in one lane must use the same evaluator and verifier contract")
            if job.cohort_id:
                cohort_lane = (
                    job.lane_id,
                    job.fixture_digest,
                    job.seed,
                    job.evaluator_epoch,
                    job.verifier_contract_ref,
                    stable_digest(job.remote.to_dict() if job.remote is not None else None),
                )
                prior_lane = cohort_lanes.setdefault(job.cohort_id, cohort_lane)
                if prior_lane != cohort_lane:
                    raise ValueError("campaign jobs in one cohort must use the same complete evaluation lane")
        return self


@dataclass(frozen=True, slots=True)
class CampaignRunOutcome:
    report: CampaignModeReport
    report_path: Path
    scheduler_report_path: Path
    event_log_path: Path


@dataclass(frozen=True, slots=True)
class CampaignEvaluationIdentity:
    """Canonical evaluator and verifier identity for one campaign runtime."""

    evaluator_epoch: str
    verifier_contract_ref: str


class ScenarioCampaignWorker:
    """Execute scheduler assignments through the configured real data plane."""

    def __init__(
        self,
        *,
        settings: AppSettings,
        runtime: ExecutionRuntime,
        scenario_name: str,
        results_root: Path,
    ) -> None:
        scenario_type = resolve_scenario_class(scenario_name, settings.knowledge_root)
        if scenario_type is None:
            raise ValueError(f"unknown campaign scenario: {scenario_name}")
        self._scenario_type = scenario_type
        self._runtime = runtime
        self._results_root = results_root
        self._results_root.mkdir(parents=True, exist_ok=True)

    def execute(self, assignment: CampaignAssignment) -> CampaignJobResult:
        payload = assignment.job.payload
        strategy = payload.get("strategy")
        if not isinstance(strategy, dict):
            raise TypeError("campaign job strategy must be a mapping")
        seed = payload.get("seed")
        if not isinstance(seed, int) or isinstance(seed, bool):
            raise TypeError("campaign job seed must be an integer")
        started = time.monotonic()
        # Scenario implementations are allowed to keep mutable match state.
        # A campaign worker may execute assignments concurrently, so every job
        # receives a fresh scenario instance instead of sharing one object.
        fixture_scenario: ScenarioInterface = self._scenario_type()
        fixture = materialize_runtime_fixture(fixture_scenario, seed)
        if assignment.job.lane.seeds != (str(seed),):
            raise ValueError("campaign job lane seed identity does not match its execution seed")
        if assignment.job.lane.fixture_digest != fixture.digest:
            raise ValueError("campaign job fixture digest does not match the actual scenario fixture")
        scenario: ScenarioInterface = self._scenario_type()
        remote_requirements = _campaign_remote.remote_requirements_from_payload(payload)
        task_id = (
            _assignment_task_id(assignment) if callable(getattr(self._runtime.remote_adapter, "cancel_request", None)) else None
        )
        try:
            output = self._runtime.supervisor.run(
                scenario,
                ExecutionInput(
                    strategy=strategy,
                    seed=seed,
                    task_id=task_id,
                    fixture_state=fixture.state,
                    fixture_observation=fixture.observation,
                    fixture_digest=fixture.digest,
                    limits=ExecutionLimits(
                        timeout_seconds=_positive_float(payload.get("timeout_seconds"), "timeout_seconds"),
                        max_memory_mb=_positive_int(payload.get("max_memory_mb"), "max_memory_mb"),
                        network_access=bool(payload.get("network_access", False)),
                    ),
                    remote_requirements=remote_requirements,
                ),
            )
        except Exception as exc:
            remote_result = self._runtime.take_remote_result(task_id) if task_id is not None else None
            if remote_result is None:
                raise
            output_path = self._result_path(assignment.job.job_id, assignment.lease.lease_id)
            artifact = _campaign_remote.remote_result_dict(remote_result)
            result = _campaign_remote.campaign_result_with_reservation(
                remote_result,
                assignment.job.reservation,
                output_ref=str(output_path),
            )
            if remote_result.succeeded:
                detail = f"remote result validation failed: {type(exc).__name__}: {exc}"
                artifact["validation_error"] = detail
                result = replace(
                    result,
                    outcome="infrastructure_failure",
                    detail=detail,
                    metadata={**result.metadata, "remote_result_validation_error": detail},
                )
            write_json(output_path, artifact)
            return result
        elapsed = max(0.0, time.monotonic() - started)
        remote_result = self._runtime.take_remote_result(task_id) if task_id is not None else None
        output_path = self._result_path(assignment.job.job_id, assignment.lease.lease_id)
        write_json(output_path, _execution_output_dict(output, remote_result=remote_result))
        passed = output.result.passed_validation
        reservation = assignment.job.reservation
        if remote_result is not None:
            result = _campaign_remote.campaign_result_with_reservation(
                remote_result,
                reservation,
                output_ref=str(output_path),
            )
            return replace(
                result,
                outcome="candidate_success" if passed else "candidate_failure",
                detail=output.result.summary,
                metadata={**result.metadata, "score": output.result.score, "seed": seed},
                retryable=False,
            )
        return CampaignJobResult(
            outcome="candidate_success" if passed else "candidate_failure",
            # ExecutionOutput does not expose model/provider usage. Charge the
            # admitted reservation for otherwise unobservable dimensions so a
            # sequence of successful jobs cannot bypass durable budget caps.
            consumed=SchedulerBudget(
                tokens=reservation.tokens,
                wall_seconds=elapsed,
                compute_units=reservation.compute_units,
                jobs=max(1, reservation.jobs),
                shared_evidence_tokens=reservation.shared_evidence_tokens,
            ),
            output_ref=str(output_path),
            detail=output.result.summary,
            cleanup_succeeded=True,
            metadata={"score": output.result.score, "seed": seed},
            retryable=False,
        )

    def cancel(self, assignment: CampaignAssignment) -> bool:
        cancel_request = getattr(self._runtime.remote_adapter, "cancel_request", None)
        if not callable(cancel_request):
            return False
        return bool(cancel_request(_assignment_task_id(assignment)))

    def _result_path(self, job_id: str, lease_id: str) -> Path:
        return self._results_root / f"{stable_digest({'job_id': job_id, 'lease_id': lease_id})}.json"


def run_campaign_plan(
    plan: CampaignPlan,
    settings: AppSettings,
    *,
    state_root: Path | None = None,
) -> CampaignRunOutcome:
    """Execute or resume one durable campaign plan."""

    evaluation_identity = derive_campaign_evaluation_identity(settings, plan.scenario_name)
    _validate_campaign_evaluation_identity(plan, evaluation_identity)
    runtime = build_execution_runtime(settings)
    requirements_by_job = {item.job_id: _campaign_remote_requirements(settings, item) for item in plan.jobs}
    _validate_campaign_remote_requirements(plan, runtime, requirements_by_job)
    root = (state_root or settings.runs_root / "campaigns") / stable_digest(
        {"campaign_id": plan.campaign_id, "run_id": plan.run_id}
    )
    root.mkdir(parents=True, exist_ok=True)
    plan_artifact_path = root / "campaign-plan.json"
    plan_artifact_digest = _persist_campaign_plan(plan_artifact_path, plan)
    event_log_path = root / "scheduler-events.jsonl"
    audit_checkpoints = build_live_campaign_auditor(
        settings,
        identity=CampaignAuditPacketIdentity(
            campaign_id=plan.campaign_id,
            run_id=plan.run_id,
            scenario_name=plan.scenario_name,
            artifact_uri=str(plan_artifact_path),
            artifact_digest=plan_artifact_digest,
            evaluator_epoch=evaluation_identity.evaluator_epoch,
            verifier_contract_ref=evaluation_identity.verifier_contract_ref,
        ),
        store_root=settings.runs_root / "campaign-audits",
        scenario_name=plan.scenario_name,
    )
    worker = ScenarioCampaignWorker(
        settings=settings,
        runtime=runtime,
        scenario_name=plan.scenario_name,
        results_root=root / "results",
    )
    scheduler = build_campaign_scheduler_runtime(
        CampaignSchedulerRuntimePlan(
            campaign_id=plan.campaign_id,
            budget=plan.budget.scheduler_budget(),
            branch_budgets={key: value.scheduler_budget() for key, value in plan.branch_budgets.items()},
            jobs=tuple(
                _job_request(
                    plan,
                    item,
                    remote_requirements=requirements_by_job[item.job_id],
                    evaluation_identity=evaluation_identity,
                    settings=settings,
                )
                for item in plan.jobs
            ),
            lease_seconds=plan.lease_seconds,
            max_concurrency=plan.max_concurrency,
            identity={"run_id": plan.run_id, "scenario_name": plan.scenario_name},
        ),
        store=CampaignSchedulerEventStore(event_log_path),
        workers=_campaign_worker_bindings(
            settings,
            runtime,
            plan,
            worker,
            requirements_by_job,
        ),
        audit_checkpoints=audit_checkpoints,
    )
    scheduler.run_until_idle(
        max_waves=sum(item.max_attempts for item in plan.jobs),
        timeout_seconds=_campaign_drain_timeout(plan, settings),
    )
    scheduler_report = scheduler.report()
    scheduler_report_path = root / "scheduler-report.json"
    write_json(scheduler_report_path, asdict(scheduler_report))
    report = _campaign_mode_report(plan, scheduler, scheduler_report_path, evaluation_identity=evaluation_identity)
    report_storage_id = stable_digest({"campaign_report_run_id": plan.run_id})
    report_path = write_campaign_mode_report(settings.knowledge_root, plan.scenario_name, report_storage_id, report)
    return CampaignRunOutcome(
        report=report,
        report_path=report_path,
        scheduler_report_path=scheduler_report_path,
        event_log_path=event_log_path,
    )


def load_campaign_plan(path: Path) -> CampaignPlan:
    data = read_json(path)
    if not isinstance(data, dict):
        raise TypeError("campaign plan must be a JSON object")
    return CampaignPlan.from_dict(data)


def _job_request(
    plan: CampaignPlan,
    item: CampaignPlanJob,
    *,
    remote_requirements: RemoteExecutionRequirements | None = None,
    evaluation_identity: CampaignEvaluationIdentity | None = None,
    settings: AppSettings | None = None,
) -> CampaignJobRequest:
    evaluator_epoch = evaluation_identity.evaluator_epoch if evaluation_identity else item.evaluator_epoch
    verifier_contract = evaluation_identity.verifier_contract_ref if evaluation_identity else item.verifier_contract_ref
    return CampaignJobRequest(
        job_id=item.job_id,
        idempotency_key=item.idempotency_key,
        campaign_id=plan.campaign_id,
        branch_id=item.branch_id,
        job_kind=item.job_kind,
        lane=EvaluationLaneIdentity(
            lane_id=item.lane_id,
            fixture_digest=item.fixture_digest,
            seeds=(str(item.seed),),
            evaluator_epoch=evaluator_epoch,
            verifier_contract_ref=verifier_contract,
            execution_environment_digest=(
                stable_digest(_campaign_remote.remote_requirements_payload(remote_requirements))
                if remote_requirements is not None
                else ""
            ),
        ),
        resources=_campaign_remote.job_resources(remote_requirements),
        required_capabilities=_campaign_remote.job_capabilities(remote_requirements),
        reservation=_campaign_job_reservation(item, remote_requirements, settings=settings),
        max_attempts=item.max_attempts,
        cohort_id=item.cohort_id,
        prefer_warm_reuse=item.prefer_warm_reuse,
        retry_expired_lease=remote_requirements is None,
        payload={
            "strategy": item.strategy,
            "seed": item.seed,
            "timeout_seconds": item.timeout_seconds,
            "max_memory_mb": item.max_memory_mb,
            "network_access": item.network_access,
            "objective": item.objective,
            "remote_requirements": (
                _campaign_remote.remote_requirements_payload(remote_requirements) if remote_requirements is not None else None
            ),
        },
    )


def _campaign_mode_report(
    plan: CampaignPlan,
    scheduler: CampaignScheduler,
    scheduler_report_path: Path,
    *,
    evaluation_identity: CampaignEvaluationIdentity | None = None,
) -> CampaignModeReport:
    by_branch: dict[str, list[CampaignPlanJob]] = defaultdict(list)
    for item in plan.jobs:
        by_branch[item.branch_id].append(item)
    branches: list[dict[str, Any]] = []
    for branch_id, jobs in sorted(by_branch.items()):
        results = [scheduler.job_result(item.job_id) for item in jobs]
        statuses = [scheduler.job_status(item.job_id) for item in jobs]
        scores = [score for result in results if result is not None and (score := _result_score(result)) is not None]
        terminal_state = _branch_terminal_state(statuses)
        consumed = scheduler.consumed_for_branch(plan.campaign_id, branch_id)
        branches.append(
            {
                "branch_id": branch_id,
                "parent_branch_id": None,
                "hypothesis_node_id": None,
                "objective": jobs[0].objective,
                "budget": _branch_budget_dict(plan.branch_budgets.get(branch_id, plan.budget)),
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": consumed.tokens,
                    "evaluations": consumed.jobs,
                    "runner_seconds": consumed.wall_seconds,
                },
                "terminal_state": terminal_state,
                "score": max(scores) if scores and terminal_state == "succeeded" else None,
                "verifier_passed": all(status == "succeeded" for status in statuses),
                "terminal_reason": ", ".join(sorted(set(statuses))),
            }
        )
    terminal = _campaign_terminal_state(branches)
    lanes: dict[str, dict[str, Any]] = {}
    for item in plan.jobs:
        lane = lanes.setdefault(
            item.lane_id,
            {
                "lane_id": item.lane_id,
                "label": item.lane_id,
                "verifier_contract_ref": (
                    evaluation_identity.verifier_contract_ref if evaluation_identity is not None else item.verifier_contract_ref
                ),
                "seeds": [],
                "holdout_refs": [],
                "weight": 1.0,
            },
        )
        lane["seeds"].append(str(item.seed))
    return build_campaign_mode_report(
        campaign_id=plan.campaign_id,
        run_id=plan.run_id,
        scenario_name=plan.scenario_name,
        terminal_state=terminal,
        branch_budget_defaults=_branch_budget_dict(plan.budget),
        eval_lanes=list(lanes.values()),
        branches=branches,
        shared_evidence=[],
        linked_reports={
            "progress_report_uri": None,
            "utilization_report_uri": str(scheduler_report_path),
            "negative_result_ledger_uri": None,
        },
    )


def _branch_terminal_state(statuses: Sequence[str]) -> str:
    if statuses and all(status == "succeeded" for status in statuses):
        return "succeeded"
    if any(status == "budget_exhausted" for status in statuses):
        return "budget_exhausted"
    if any(status == "canceled" for status in statuses):
        return "canceled"
    if any(status in {"queued", "leased", "canceling"} for status in statuses):
        return "running"
    return "failed"


def _campaign_terminal_state(branches: Sequence[dict[str, Any]]) -> CampaignTerminalState:
    states = {str(branch["terminal_state"]) for branch in branches}
    if states and states <= {"succeeded"}:
        return "completed"
    if "failed" in states:
        return "failed"
    if states & {"pending", "running", "continued"}:
        return "active"
    if "budget_exhausted" in states:
        return "budget_exhausted"
    if "canceled" in states:
        return "canceled"
    return "failed"


def _branch_budget_dict(budget: CampaignPlanBudget) -> dict[str, Any]:
    return {
        "max_tokens": budget.tokens or None,
        "max_seconds": budget.wall_seconds or None,
        "max_evaluations": budget.jobs or None,
    }


def _campaign_job_reservation(
    item: CampaignPlanJob,
    remote_requirements: RemoteExecutionRequirements | None = None,
    *,
    settings: AppSettings | None = None,
) -> SchedulerBudget:
    """Prevent a plan from under-declaring costs the worker always incurs."""

    declared = item.reservation.scheduler_budget()
    accelerator = remote_requirements.resources.accelerator if remote_requirements is not None else None
    envelope = _campaign_remote.campaign_execution_envelope(
        settings,
        item.timeout_seconds,
        remote_execution=remote_requirements is not None,
    )
    required_compute = envelope.provider_seconds * accelerator.count if accelerator is not None else 0.0
    return SchedulerBudget(
        tokens=declared.tokens,
        wall_seconds=max(declared.wall_seconds, envelope.wall_seconds),
        compute_units=max(declared.compute_units, required_compute),
        jobs=max(declared.jobs, 1),
        shared_evidence_tokens=declared.shared_evidence_tokens,
    )


def _campaign_remote_requirements(
    settings: AppSettings,
    item: CampaignPlanJob,
) -> RemoteExecutionRequirements | None:
    configured = item.remote
    if configured is None and settings.executor_mode != "primeintellect":
        return None
    image = (
        configured.image.strip() if configured is not None and configured.image.strip() else settings.primeintellect_docker_image
    )
    require_pinned_runtime_image(image)
    accelerator = None
    if configured is not None and configured.accelerator is not None:
        accelerator = RemoteAcceleratorRequest(
            kind=configured.accelerator.kind,
            count=configured.accelerator.count,
        )
    configured_memory = (
        configured.memory_gb if configured is not None and configured.memory_gb is not None else settings.primeintellect_memory_gb
    )
    effective_memory = min(configured_memory, max(0.25, float(item.max_memory_mb) / 1024.0))
    return RemoteExecutionRequirements(
        image=image,
        resources=RemoteResourceRequest(
            cpu_cores=(
                configured.cpu_cores if configured and configured.cpu_cores is not None else settings.primeintellect_cpu_cores
            ),
            memory_gb=effective_memory,
            disk_gb=(
                configured.disk_gb if configured and configured.disk_gb is not None else settings.primeintellect_disk_size_gb
            ),
            accelerator=accelerator,
        ),
        region=(configured.region.strip() or None) if configured is not None else None,
        required_telemetry=(
            frozenset(configured.required_telemetry)  # type: ignore[arg-type]
            if configured is not None
            else frozenset()
        ),
    )


def _validate_campaign_remote_requirements(
    plan: CampaignPlan,
    runtime: ExecutionRuntime,
    requirements_by_job: Mapping[str, RemoteExecutionRequirements | None],
) -> None:
    if any(item.remote is not None for item in plan.jobs) and runtime.remote_adapter is None:
        raise ValueError("campaign remote requirements require a remote executor")
    validate = getattr(runtime.remote_adapter, "validate_requirements", None)
    for item in plan.jobs:
        requirements = requirements_by_job[item.job_id]
        if requirements is None:
            continue
        if not callable(validate):
            raise ValueError("configured remote executor cannot validate campaign placement requirements")
        validate(requirements)
    cohort_requirements: dict[str, str] = {}
    for item in plan.jobs:
        if not item.cohort_id:
            continue
        requirements = requirements_by_job[item.job_id]
        digest = (
            stable_digest(_campaign_remote.remote_requirements_payload(requirements)) if requirements is not None else "local"
        )
        prior = cohort_requirements.setdefault(item.cohort_id, digest)
        if prior != digest:
            raise ValueError("matched cohort jobs must use the same resolved remote requirements")


def _campaign_worker_bindings(
    settings: AppSettings,
    runtime: ExecutionRuntime,
    plan: CampaignPlan,
    worker: ScenarioCampaignWorker,
    requirements_by_job: Mapping[str, RemoteExecutionRequirements | None],
) -> tuple[CampaignWorkerBinding, ...]:
    profiles: dict[str, RemoteExecutionRequirements | None] = {}
    for requirements in requirements_by_job.values():
        digest = (
            stable_digest(_campaign_remote.remote_requirements_payload(requirements)) if requirements is not None else "local"
        )
        profiles.setdefault(digest, requirements)
    bindings: list[CampaignWorkerBinding] = []
    for digest, requirements in sorted(profiles.items()):
        labels = {"executor_mode": settings.executor_mode, "requirements_digest": digest}
        if requirements is not None:
            labels.update(
                {
                    "image": requirements.image,
                    "region": requirements.region or "provider-selected",
                    "accelerator_kind": (
                        requirements.resources.accelerator.kind if requirements.resources.accelerator is not None else "cpu"
                    ),
                }
            )
        bindings.append(
            CampaignWorkerBinding(
                descriptor=WorkerDescriptor(
                    worker_id=f"{settings.executor_mode}-{digest[:12]}",
                    runtime=settings.executor_mode,
                    resources=_campaign_remote.worker_resources(requirements, plan.max_concurrency),
                    capabilities=_campaign_remote.job_capabilities(requirements),
                    sandbox_features=frozenset(_sandbox_features(settings)),
                    locality="remote" if runtime.remote_adapter is not None else "local",
                    concurrency=plan.max_concurrency,
                    environment_labels=labels,
                ),
                executor=CallableCampaignWorker(worker.execute, worker.cancel),
            )
        )
    return tuple(bindings)


def _campaign_drain_timeout(plan: CampaignPlan, settings: AppSettings | None = None) -> float:
    attempt_seconds = sum(
        _campaign_remote.campaign_execution_envelope(
            settings,
            item.timeout_seconds,
            remote_execution=settings is not None and settings.executor_mode == "primeintellect",
        ).wall_seconds
        * item.max_attempts
        for item in plan.jobs
    )
    replay_grace = plan.lease_seconds * sum(item.max_attempts for item in plan.jobs)
    timeout = attempt_seconds + replay_grace + 5.0
    if not math.isfinite(timeout):
        raise ValueError("campaign plan aggregate drain timeout must be finite")
    return max(1.0, timeout)


def _sandbox_features(settings: AppSettings) -> tuple[str, ...]:
    if settings.executor_mode == "primeintellect":
        return ("cold_ephemeral",)
    if settings.executor_mode == "monty":
        return ("in_process_sandbox",)
    return ()


def _execution_output_dict(
    output: ExecutionOutput,
    *,
    remote_result: RemoteExecutionResult | None = None,
) -> dict[str, Any]:
    payload = {
        "result": output.result.model_dump(mode="json"),
        "replay": output.replay.model_dump(mode="json"),
    }
    if remote_result is not None:
        payload["remote_execution"] = _campaign_remote.remote_result_dict(remote_result)
    return payload


def _assignment_task_id(assignment: CampaignAssignment) -> str:
    """Bind remote execution and cancellation to one lease-unique identity."""

    identity = {
        "campaign_id": assignment.job.campaign_id,
        "job_id": assignment.job.job_id,
        "lease_id": assignment.lease.lease_id,
    }
    return f"campaign:{stable_digest(identity)}"


def _result_score(result: CampaignJobResult) -> float | None:
    value = result.metadata.get("score")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _positive_float(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise TypeError(f"campaign job {field} must be positive")
    return float(value)


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise TypeError(f"campaign job {field} must be a positive integer")
    return value


def derive_campaign_evaluation_identity(settings: AppSettings, scenario_name: str) -> CampaignEvaluationIdentity:
    """Derive the actual scenario evaluator identity used by campaign workers."""

    scenario_type = resolve_scenario_class(scenario_name, settings.knowledge_root)
    if scenario_type is None:
        raise ValueError(f"unknown campaign scenario: {scenario_name}")
    evaluator_epoch = evaluator_epoch_for(scenario_type(), settings)
    return CampaignEvaluationIdentity(
        evaluator_epoch=evaluator_epoch,
        verifier_contract_ref=f"runtime-scenario-v1:{scenario_name}:{evaluator_epoch}",
    )


def _validate_campaign_evaluation_identity(
    plan: CampaignPlan,
    identity: CampaignEvaluationIdentity,
) -> None:
    for item in plan.jobs:
        if item.evaluator_epoch != identity.evaluator_epoch:
            raise ValueError(f"campaign job {item.job_id!r} evaluator_epoch does not match the active scenario evaluator")
        if item.verifier_contract_ref != identity.verifier_contract_ref:
            raise ValueError(f"campaign job {item.job_id!r} verifier_contract_ref does not match the runtime verifier contract")


def _persist_campaign_plan(path: Path, plan: CampaignPlan) -> str:
    payload = plan.to_dict()
    digest = stable_digest(payload)
    if path.exists():
        if read_json(path) != payload:
            raise ValueError("durable campaign plan artifact conflicts with the requested plan")
    else:
        write_json(path, payload)
    return digest


__all__ = [
    "CampaignPlan",
    "CampaignPlanAccelerator",
    "CampaignPlanBudget",
    "CampaignPlanJob",
    "CampaignPlanRemoteRequirements",
    "CampaignEvaluationIdentity",
    "CampaignRunOutcome",
    "ScenarioCampaignWorker",
    "derive_campaign_evaluation_identity",
    "load_campaign_plan",
    "run_campaign_plan",
]
