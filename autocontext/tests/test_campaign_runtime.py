from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from autocontext.audit import CampaignAuditPacketIdentity
from autocontext.cli import app
from autocontext.config.settings import AppSettings
from autocontext.context_bundles.models import stable_digest
from autocontext.context_bundles.runtime_evaluator import materialize_runtime_fixture, runtime_fixture_digest
from autocontext.execution.campaign_runtime import (
    CampaignPlan,
    ScenarioCampaignWorker,
    _campaign_drain_timeout,
    _campaign_mode_report,
    _job_request,
    derive_campaign_evaluation_identity,
    load_campaign_plan,
    run_campaign_plan,
)
from autocontext.execution.campaign_scheduler import CampaignScheduler, CampaignSchedulerEventStore
from autocontext.execution.campaign_scheduler_models import (
    CampaignAssignment,
    CampaignJobRequest,
    CampaignJobResult,
    CampaignLease,
    EvaluationLaneIdentity,
    SchedulerBudget,
    WorkerDescriptor,
)
from autocontext.execution.remote_execution import (
    RemoteCleanupOutcome,
    RemoteExecutionResult,
    RemoteResourceUsage,
)
from autocontext.execution.runtime_factory import ExecutionRuntime
from autocontext.execution.supervisor import ExecutionSupervisor
from autocontext.runtime_images import PINNED_PYTHON_RUNTIME_IMAGE
from autocontext.scenarios.base import ReplayEnvelope, Result
from autocontext.scenarios.othello import OthelloScenario


def _plan(settings: AppSettings | None = None) -> CampaignPlan:
    evaluation_identity = derive_campaign_evaluation_identity(settings or AppSettings(), "othello")
    fixture_11 = materialize_runtime_fixture(OthelloScenario(), 11).digest
    fixture_12 = materialize_runtime_fixture(OthelloScenario(), 12).digest
    return CampaignPlan.from_dict(
        {
            "schema_version": 1,
            "campaign_id": "campaign-live",
            "run_id": "run-live",
            "scenario_name": "othello",
            "budget": {"jobs": 2, "wall_seconds": 60},
            "branch_budgets": {
                "branch-a": {"jobs": 1},
                "branch-b": {"jobs": 1},
            },
            "max_concurrency": 2,
            "lease_seconds": 5,
            "jobs": [
                {
                    "job_id": "trial-a",
                    "idempotency_key": "trial-a-v1",
                    "branch_id": "branch-a",
                    "objective": "favor mobility",
                    "strategy": {"mobility_weight": 1.0, "corner_weight": 0.5, "stability_weight": 0.5},
                    "seed": 11,
                    "lane_id": "confirmation",
                    "fixture_digest": fixture_11,
                    "evaluator_epoch": evaluation_identity.evaluator_epoch,
                    "verifier_contract_ref": evaluation_identity.verifier_contract_ref,
                    "reservation": {"jobs": 1},
                },
                {
                    "job_id": "trial-b",
                    "idempotency_key": "trial-b-v1",
                    "branch_id": "branch-b",
                    "objective": "favor corners",
                    "strategy": {"mobility_weight": 0.5, "corner_weight": 1.0, "stability_weight": 0.5},
                    "seed": 12,
                    "lane_id": "confirmation",
                    "fixture_digest": fixture_12,
                    "evaluator_epoch": evaluation_identity.evaluator_epoch,
                    "verifier_contract_ref": evaluation_identity.verifier_contract_ref,
                    "reservation": {"jobs": 1},
                },
            ],
        }
    )


def test_campaign_runtime_executes_real_scenario_and_persists_reports(tmp_path: Path) -> None:
    settings = AppSettings(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        event_stream_path=tmp_path / "events.ndjson",
    )
    outcome = run_campaign_plan(_plan(settings), settings, state_root=tmp_path / "campaign-state")

    assert outcome.report.terminal_state == "completed"
    assert outcome.report.branch_summary.succeeded == 2
    assert outcome.report.final_recommendation is not None
    assert outcome.report_path.exists()
    assert outcome.scheduler_report_path.exists()
    scheduler_report = json.loads(outcome.scheduler_report_path.read_text(encoding="utf-8"))
    assert scheduler_report["succeeded"] == 2
    assert scheduler_report["consumed_by_campaign"]["campaign-live"]["jobs"] == 2
    assert len(list((tmp_path / "campaign-state").rglob("results/*.json"))) == 2


def test_campaign_plan_round_trip_and_cli_entrypoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(_plan().to_dict()), encoding="utf-8")
    assert load_campaign_plan(plan_path) == _plan()

    monkeypatch.setenv("AUTOCONTEXT_RUNS_ROOT", str(tmp_path / "runs"))
    monkeypatch.setenv("AUTOCONTEXT_KNOWLEDGE_ROOT", str(tmp_path / "knowledge"))
    result = CliRunner().invoke(
        app,
        ["campaign", "run", str(plan_path), "--state-root", str(tmp_path / "state"), "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["campaign_id"] == "campaign-live"
    assert payload["terminal_state"] == "completed"


def test_campaign_runtime_normalizes_mandatory_job_and_timeout_reservations() -> None:
    plan = _plan()
    item_reservation = plan.jobs[0].reservation.model_copy(update={"jobs": 0, "wall_seconds": 0.0})
    item = plan.jobs[0].model_copy(
        update={
            "timeout_seconds": 17.0,
            "reservation": item_reservation,
        }
    )
    assert item_reservation.jobs == 0

    request = _job_request(plan, item)

    assert request.reservation.jobs == 1
    assert request.reservation.wall_seconds == 17.0


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("budget", "wall_seconds"), float("inf")),
        (("budget", "compute_units"), float("nan")),
        (("budget", "tokens"), True),
        (("jobs", 0, "timeout_seconds"), float("inf")),
        (("jobs", 0, "max_attempts"), True),
        (("jobs", 0, "max_memory_mb"), 128.5),
        (("max_concurrency",), True),
        (("lease_seconds",), float("nan")),
    ],
)
def test_campaign_plan_rejects_nonfinite_or_coerced_budget_and_runtime_values(
    path: tuple[str | int, ...],
    value: object,
) -> None:
    data = _plan().to_dict()
    target: Any = data
    for segment in path[:-1]:
        target = target[segment]
    target[path[-1]] = value

    with pytest.raises(ValueError):
        CampaignPlan.from_dict(data)


def test_campaign_drain_timeout_rejects_finite_inputs_that_overflow_in_aggregate() -> None:
    plan = _plan()
    jobs = [item.model_copy(update={"timeout_seconds": 1e308}) for item in plan.jobs]

    with pytest.raises(ValueError, match="aggregate drain timeout must be finite"):
        _campaign_drain_timeout(plan.model_copy(update={"jobs": jobs}))


@pytest.mark.parametrize("field", ["evaluator_epoch", "verifier_contract_ref"])
def test_campaign_runtime_rejects_self_declared_evaluator_identity_before_state_creation(
    tmp_path: Path,
    field: str,
) -> None:
    settings = AppSettings(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        event_stream_path=tmp_path / "events.ndjson",
    )
    plan = _plan(settings)
    jobs = [plan.jobs[0].model_copy(update={field: "self-declared-wrong"}), *plan.jobs[1:]]
    state_root = tmp_path / "campaign-state"

    with pytest.raises(ValueError, match=field):
        run_campaign_plan(plan.model_copy(update={"jobs": jobs}), settings, state_root=state_root)

    assert not state_root.exists()


def test_campaign_runtime_persists_immutable_plan_identity_for_audit_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        event_stream_path=tmp_path / "events.ndjson",
    )
    plan = _plan(settings)
    captured: list[CampaignAuditPacketIdentity] = []

    def capture_auditor(
        _settings: AppSettings,
        *,
        identity: CampaignAuditPacketIdentity,
        **_kwargs: object,
    ) -> None:
        captured.append(identity)

    monkeypatch.setattr("autocontext.execution.campaign_runtime.build_live_campaign_auditor", capture_auditor)
    outcome = run_campaign_plan(plan, settings, state_root=tmp_path / "campaign-state")

    identity = captured[0]
    assert identity.artifact_digest == stable_digest(plan.to_dict())
    artifact_path = Path(identity.artifact_uri)
    assert artifact_path.name == "campaign-plan.json"
    assert json.loads(artifact_path.read_text(encoding="utf-8")) == plan.to_dict()
    assert (
        outcome.report.eval_lanes[0].verifier_contract_ref
        == derive_campaign_evaluation_identity(settings, "othello").verifier_contract_ref
    )


def test_campaign_plan_rejects_ambiguous_job_and_cohort_identities() -> None:
    duplicate = _plan().to_dict()
    duplicate["jobs"][1]["idempotency_key"] = duplicate["jobs"][0]["idempotency_key"]
    with pytest.raises(ValueError, match="idempotency_key values must be unique"):
        CampaignPlan.from_dict(duplicate)

    mismatched_cohort = _plan().to_dict()
    for job in mismatched_cohort["jobs"]:
        job["cohort_id"] = "matched-cohort"
    with pytest.raises(ValueError, match="same complete evaluation lane"):
        CampaignPlan.from_dict(mismatched_cohort)


def test_campaign_runtime_wires_enabled_durable_auditor(tmp_path: Path) -> None:
    settings = AppSettings(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        event_stream_path=tmp_path / "events.ndjson",
        agent_provider="deterministic",
        campaign_auditor_enabled=True,
        campaign_auditor_provider="deterministic",
        campaign_auditor_model="independent-auditor",
        campaign_auditor_proposer_provider="deterministic",
        campaign_auditor_proposer_model="proposer",
        campaign_auditor_max_calls_per_campaign=2,
    )
    run_campaign_plan(_plan(settings), settings, state_root=tmp_path / "campaign-state")

    audit_records = list((settings.runs_root / "campaign-audits").rglob("*.json"))
    assert audit_records
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in audit_records]
    records = [payload for payload in payloads if "audit" in payload]
    assert records
    assert records[-1]["audit"]["checkpoint"] == "final_completion"
    assert records[-1]["audit"]["model_call_attempted"] is True


def test_campaign_runtime_enforces_a_campaign_wide_audit_budget_across_runs(tmp_path: Path) -> None:
    from autocontext.audit import CampaignAuditStore

    settings = AppSettings(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        event_stream_path=tmp_path / "events.ndjson",
        agent_provider="deterministic",
        campaign_auditor_enabled=True,
        campaign_auditor_provider="deterministic",
        campaign_auditor_model="independent-auditor",
        campaign_auditor_proposer_provider="deterministic",
        campaign_auditor_proposer_model="proposer",
        campaign_auditor_max_calls_per_campaign=1,
    )
    first = _plan(settings)
    second = first.model_copy(update={"run_id": "run-live-second"})

    run_campaign_plan(first, settings, state_root=tmp_path / "campaign-state")
    run_campaign_plan(second, settings, state_root=tmp_path / "campaign-state")

    store = CampaignAuditStore(settings.runs_root / "campaign-audits")
    assert store.call_count(first.campaign_id) == 1
    assert len(store.records(first.campaign_id)) == 1


def test_live_auditor_runtime_rejects_any_actual_proposer_route_match(tmp_path: Path) -> None:
    from autocontext.audit import CampaignAuditPacketIdentity
    from autocontext.campaign_audit_runtime import build_live_campaign_auditor

    settings = AppSettings(
        campaign_auditor_enabled=True,
        campaign_auditor_provider="deterministic",
        campaign_auditor_model="architect-model",
        campaign_auditor_proposer_provider="deterministic",
        campaign_auditor_proposer_model="legacy-model",
    )
    identity = CampaignAuditPacketIdentity(
        campaign_id="campaign-live",
        run_id="run-live",
        scenario_name="othello",
        artifact_uri="artifact://campaign-live/checkpoint",
    )

    with pytest.raises(ValueError, match="every proposer route"):
        build_live_campaign_auditor(
            settings,
            identity=identity,
            store_root=tmp_path,
            proposer_routes=[("deterministic", "coach-model"), ("deterministic", "architect-model")],
        )


def test_live_auditor_uses_dedicated_route_without_agent_endpoint_or_key_leak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autocontext.audit import CampaignAuditPacketIdentity
    from autocontext.campaign_audit_runtime import build_live_campaign_auditor

    captured: dict[str, Any] = {}

    class _Provider:
        def complete(self, _prompt: str, **_kwargs: Any) -> str:
            return "{}"

    def capture_provider(**kwargs: Any) -> _Provider:
        captured.update(kwargs)
        return _Provider()

    monkeypatch.setattr("autocontext.providers.registry.create_provider", capture_provider)
    settings = AppSettings(
        agent_provider="openrouter",
        agent_api_key="agent-route-secret",
        agent_base_url="https://private-agent.example.invalid/v1",
        campaign_auditor_enabled=True,
        campaign_auditor_provider="openrouter",
        campaign_auditor_model="independent-model",
        campaign_auditor_base_url="https://AUDITOR.example.invalid:443/v1/",
        campaign_auditor_api_key="auditor-only-secret",
        campaign_auditor_proposer_provider="openai-compatible",
        campaign_auditor_proposer_model="proposer-model",
    )
    checkpoint_runner = build_live_campaign_auditor(
        settings,
        identity=CampaignAuditPacketIdentity(
            campaign_id="campaign-live",
            run_id="run-live",
            scenario_name="othello",
            artifact_uri="artifact://campaign-live/checkpoint",
        ),
        store_root=tmp_path,
    )

    assert checkpoint_runner is not None
    assert captured == {
        "provider_type": "openrouter",
        "api_key": "auditor-only-secret",
        "base_url": "https://AUDITOR.example.invalid:443/v1/",
        "model": "independent-model",
    }
    route = checkpoint_runner.auditor.config.auditor_route
    assert route is not None
    assert route.backend_identity == "endpoint:https://auditor.example.invalid/v1"
    assert "secret" not in json.dumps(checkpoint_runner.auditor.config.to_dict())


def test_live_auditor_rejects_endpoint_for_incompatible_provider(tmp_path: Path) -> None:
    from autocontext.audit import CampaignAuditPacketIdentity
    from autocontext.campaign_audit_runtime import build_live_campaign_auditor

    settings = AppSettings(
        campaign_auditor_enabled=True,
        campaign_auditor_provider="anthropic",
        campaign_auditor_model="independent-model",
        campaign_auditor_base_url="https://private-agent.example.invalid/v1",
    )

    with pytest.raises(ValueError, match="does not support a dedicated base URL"):
        build_live_campaign_auditor(
            settings,
            identity=CampaignAuditPacketIdentity(
                campaign_id="campaign-live",
                run_id="run-live",
                scenario_name="othello",
                artifact_uri="artifact://campaign-live/checkpoint",
            ),
            store_root=tmp_path,
        )


def test_auditor_route_never_falls_back_to_matching_agent_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autocontext.agents.provider_bridge import _provider_api_key, _provider_base_url

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("AUTOCONTEXT_OPENROUTER_API_KEY", raising=False)
    settings = AppSettings(
        agent_provider="openrouter",
        agent_api_key="agent-route-secret",
        agent_base_url="https://private-agent.example.invalid/v1",
        campaign_auditor_provider="openrouter",
    )

    assert _provider_api_key("openrouter", settings, role="campaign_auditor") is None
    assert _provider_base_url(settings, role="campaign_auditor") is None


def test_campaign_worker_uses_same_lease_unique_remote_id_for_run_and_cancel(tmp_path: Path) -> None:
    class CapturingExecutor:
        def __init__(self) -> None:
            self.task_ids: list[str] = []

        def execute_with_task_id(self, *args: Any, task_id: str, **kwargs: Any) -> tuple[Result, ReplayEnvelope]:
            raise AssertionError("campaign work must execute the attested fixture")

        def execute_prepared_fixture_with_task_id(
            self,
            *args: Any,
            task_id: str,
            initial_state: Any,
            **kwargs: Any,
        ) -> tuple[Result, ReplayEnvelope]:
            self.task_ids.append(task_id)
            assert initial_state is not None
            return (
                Result(score=1.0, summary="ok", passed_validation=True),
                ReplayEnvelope(scenario="othello", seed=7, frames=[], narrative="ok"),
            )

        def execute(self, *args: Any, **kwargs: Any) -> tuple[Result, ReplayEnvelope]:
            raise AssertionError("campaign remote work must use its explicit task id")

    class CapturingRemote:
        def __init__(self) -> None:
            self.canceled: list[str] = []

        def cancel_request(self, task_id: str) -> bool:
            self.canceled.append(task_id)
            return True

    executor = CapturingExecutor()
    remote = CapturingRemote()
    worker = ScenarioCampaignWorker(
        settings=AppSettings(knowledge_root=tmp_path / "knowledge"),
        runtime=ExecutionRuntime(ExecutionSupervisor(executor), remote),  # type: ignore[arg-type]
        scenario_name="othello",
        results_root=tmp_path / "results",
    )
    job = CampaignJobRequest(
        job_id="same-seed-job",
        idempotency_key="same-seed-job-v1",
        campaign_id="campaign-live",
        branch_id="branch-a",
        job_kind="trial",
        lane=EvaluationLaneIdentity(
            "confirmation",
            materialize_runtime_fixture(OthelloScenario(), 7).digest,
            ("7",),
            "epoch-1",
            "contract-1",
        ),
        reservation=SchedulerBudget(jobs=1),
        payload={"strategy": {"mobility_weight": 1.0}, "seed": 7, "timeout_seconds": 5.0, "max_memory_mb": 128},
    )
    assignment = CampaignAssignment(
        job=job,
        lease=CampaignLease(
            lease_id="lease-attempt-2",
            job_id=job.job_id,
            worker_id="prime-primary",
            attempt=2,
            issued_at=5.0,
            expires_at=10.0,
            environment_fingerprint="prime-env",
            lifecycle="cold_ephemeral",
            reuse_key="",
        ),
    )

    with pytest.raises(ValueError, match="fixture digest"):
        worker.execute(
            replace(
                assignment,
                job=replace(
                    job,
                    lane=replace(job.lane, fixture_digest="operator-supplied-unbound-fixture"),
                ),
            )
        )
    with pytest.raises(ValueError, match="lane seed"):
        worker.execute(
            replace(
                assignment,
                job=replace(job, lane=replace(job.lane, seeds=("8",))),
            )
        )
    assert executor.task_ids == []
    first_result = worker.execute(assignment)
    assert worker.cancel(assignment) is True
    retry = replace(assignment, lease=replace(assignment.lease, lease_id="lease-attempt-3", attempt=3))
    second_result = worker.execute(retry)
    assert worker.cancel(retry) is True
    assert first_result.output_ref != second_result.output_ref
    assert Path(first_result.output_ref).exists()
    assert Path(second_result.output_ref).exists()
    assert len(executor.task_ids) == 2
    assert remote.canceled == executor.task_ids
    assert "same-seed-job" not in executor.task_ids[0]


def test_campaign_worker_treats_invalid_successful_remote_payload_as_infrastructure_failure(
    tmp_path: Path,
) -> None:
    class InvalidRemotePayloadExecutor:
        def __init__(self) -> None:
            self.results: dict[str, RemoteExecutionResult] = {}

        def execute_prepared_fixture_with_task_id_and_remote_requirements(
            self,
            *args: Any,
            task_id: str,
            **kwargs: Any,
        ) -> tuple[Result, ReplayEnvelope]:
            self.results[task_id] = RemoteExecutionResult(
                task_id=task_id,
                provider="primeintellect",
                status="success",
                usage=RemoteResourceUsage(wall_seconds=3.0, accelerator_seconds=2.5),
                cleanup=RemoteCleanupOutcome(attempted=True, succeeded=True),
            )
            raise ValueError("provider payload is not a valid scenario result")

        def take_remote_result(self, task_id: str) -> RemoteExecutionResult | None:
            return self.results.pop(task_id, None)

    class CancelableRemote:
        def cancel_request(self, task_id: str) -> bool:
            del task_id
            return True

    fixture = materialize_runtime_fixture(OthelloScenario(), 17)
    executor = InvalidRemotePayloadExecutor()
    worker = ScenarioCampaignWorker(
        settings=AppSettings(knowledge_root=tmp_path / "knowledge"),
        runtime=ExecutionRuntime(ExecutionSupervisor(executor), CancelableRemote()),  # type: ignore[arg-type]
        scenario_name="othello",
        results_root=tmp_path / "results",
    )
    job = CampaignJobRequest(
        job_id="invalid-remote-payload",
        idempotency_key="invalid-remote-payload-v1",
        campaign_id="campaign-live",
        branch_id="branch-a",
        job_kind="trial",
        lane=EvaluationLaneIdentity("confirmation", fixture.digest, ("17",), "epoch-1", "contract-1"),
        reservation=SchedulerBudget(wall_seconds=10.0, compute_units=10.0, jobs=1),
        payload={
            "strategy": {"mobility_weight": 1.0},
            "seed": 17,
            "timeout_seconds": 10.0,
            "max_memory_mb": 1024,
            "remote_requirements": {
                "image": PINNED_PYTHON_RUNTIME_IMAGE,
                "resources": {
                    "cpu_cores": 1.0,
                    "memory_gb": 1.0,
                    "disk_gb": 5.0,
                    "accelerator": {"kind": "H100", "count": 1},
                },
                "region": "us-central-1",
                "required_telemetry": ["hardware_identity"],
            },
        },
    )
    assignment = CampaignAssignment(
        job,
        CampaignLease(
            lease_id="lease-invalid-payload",
            job_id=job.job_id,
            worker_id="prime-gpu",
            attempt=1,
            issued_at=0.0,
            expires_at=10.0,
            environment_fingerprint="prime-gpu-env",
            lifecycle="cold_ephemeral",
            reuse_key="",
        ),
    )

    result = worker.execute(assignment)

    assert result.outcome == "infrastructure_failure"
    assert result.consumed.compute_units == pytest.approx(2.5)
    assert "remote result validation failed" in result.detail
    artifact = json.loads(Path(result.output_ref).read_text(encoding="utf-8"))
    assert artifact["status"] == "success"
    assert artifact["validation_error"] == result.detail


def test_campaign_worker_executes_the_exact_materialized_nondeterministic_fixture(tmp_path: Path) -> None:
    class NondeterministicOthello(OthelloScenario):
        nonce = 0

        def initial_state(self, seed: int | None = None) -> dict[str, Any]:
            state = super().initial_state(seed)
            type(self).nonce += 1
            state["fixture_nonce"] = type(self).nonce
            return state

    class CapturingSupervisor:
        def __init__(self) -> None:
            self.payload: Any | None = None

        def run(self, scenario: Any, payload: Any) -> Any:
            del scenario
            self.payload = payload
            return SimpleNamespace(
                result=Result(score=1.0, summary="ok", passed_validation=True),
                replay=ReplayEnvelope(scenario="othello", seed=23, frames=[], narrative="ok"),
            )

    from types import SimpleNamespace

    NondeterministicOthello.nonce = 0
    expected = materialize_runtime_fixture(NondeterministicOthello(), 23)
    NondeterministicOthello.nonce = 0
    supervisor = CapturingSupervisor()
    worker = ScenarioCampaignWorker(
        settings=AppSettings(knowledge_root=tmp_path / "knowledge"),
        runtime=ExecutionRuntime(supervisor),  # type: ignore[arg-type]
        scenario_name="othello",
        results_root=tmp_path / "results",
    )
    worker._scenario_type = NondeterministicOthello
    job = CampaignJobRequest(
        job_id="nondeterministic",
        idempotency_key="nondeterministic-v1",
        campaign_id="campaign-live",
        branch_id="branch-a",
        job_kind="trial",
        lane=EvaluationLaneIdentity("confirmation", expected.digest, ("23",), "epoch-1", "contract-1"),
        payload={"strategy": {"mobility_weight": 1.0}, "seed": 23, "timeout_seconds": 5.0, "max_memory_mb": 128},
    )
    assignment = CampaignAssignment(
        job,
        CampaignLease(
            lease_id="lease-1",
            job_id=job.job_id,
            worker_id="local",
            attempt=1,
            issued_at=0.0,
            expires_at=5.0,
            environment_fingerprint="local",
            lifecycle="cold_ephemeral",
            reuse_key="",
        ),
    )

    worker.execute(assignment)

    assert NondeterministicOthello.nonce == 1
    assert supervisor.payload is not None
    assert supervisor.payload.fixture_state["fixture_nonce"] == 1
    assert supervisor.payload.fixture_digest == expected.digest
    assert (
        runtime_fixture_digest(
            supervisor.payload.fixture_state,
            supervisor.payload.fixture_observation,
        )
        == expected.digest
    )


def test_successful_jobs_conservatively_consume_reserved_token_and_compute_budgets(tmp_path: Path) -> None:
    plan = _plan()
    reservation = plan.jobs[0].reservation.model_copy(update={"tokens": 5, "compute_units": 1.0, "jobs": 1})
    plan = plan.model_copy(
        update={
            "campaign_id": "org/team\\campaign",
            "run_id": "runs/2026\\08",
            "budget": plan.budget.model_copy(update={"tokens": 5, "compute_units": 1.0, "jobs": 2}),
            "branch_budgets": {},
            "jobs": [item.model_copy(update={"reservation": reservation}) for item in plan.jobs],
        }
    )
    settings = AppSettings(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        event_stream_path=tmp_path / "events.ndjson",
    )

    outcome = run_campaign_plan(plan, settings, state_root=tmp_path / "campaign-state")

    scheduler_report = json.loads(outcome.scheduler_report_path.read_text(encoding="utf-8"))
    assert scheduler_report["succeeded"] == 1
    assert scheduler_report["budget_exhausted"] == 1
    assert scheduler_report["consumed_by_campaign"][plan.campaign_id]["tokens"] == 5
    assert scheduler_report["consumed_by_campaign"][plan.campaign_id]["compute_units"] == 1.0
    assert outcome.report.campaign_id == plan.campaign_id
    assert outcome.report.run_id == plan.run_id
    assert outcome.report.terminal_state == "budget_exhausted"
    assert outcome.report_path.exists()
    assert "/" not in outcome.report_path.name and "\\" not in outcome.report_path.name


def test_failed_candidates_never_become_final_recommendations(tmp_path: Path) -> None:
    source = _plan()
    plan = source.model_copy(
        update={
            "jobs": [source.jobs[0]],
            "branch_budgets": {},
        }
    )
    scheduler = CampaignScheduler(CampaignSchedulerEventStore(tmp_path / "events.jsonl"))
    scheduler.configure_campaign(plan.campaign_id, plan.budget.scheduler_budget())
    scheduler.register_worker(
        WorkerDescriptor(
            worker_id="worker",
            runtime="local",
            resources=_job_request(plan, plan.jobs[0]).resources,
            capabilities=frozenset({"scenario_evaluation"}),
        )
    )
    request = _job_request(plan, plan.jobs[0])
    scheduler.enqueue(request)
    assignment = scheduler.claim("worker")[0]
    scheduler.complete(
        assignment.lease.lease_id,
        CampaignJobResult(
            outcome="candidate_failure",
            consumed=SchedulerBudget(jobs=1),
            metadata={"score": 999.0},
        ),
    )

    report = _campaign_mode_report(plan, scheduler, tmp_path / "scheduler-report.json")

    assert report.terminal_state == "failed"
    assert report.branches[0].score is None
    assert report.branches[0].verifier_passed is False
    assert report.final_recommendation is None


def test_campaign_report_branch_usage_includes_retry_attempts(tmp_path: Path) -> None:
    source = _plan()
    job = source.jobs[0].model_copy(update={"max_attempts": 2})
    plan = source.model_copy(update={"jobs": [job], "branch_budgets": {}})
    scheduler = CampaignScheduler(CampaignSchedulerEventStore(tmp_path / "events.jsonl"))
    scheduler.configure_campaign(plan.campaign_id, plan.budget.scheduler_budget())
    request = _job_request(plan, job)
    scheduler.register_worker(
        WorkerDescriptor(
            worker_id="worker",
            runtime="local",
            resources=request.resources,
            capabilities=frozenset({"scenario_evaluation"}),
        )
    )
    scheduler.enqueue(request)
    first = scheduler.claim("worker")[0]
    scheduler.complete(
        first.lease.lease_id,
        CampaignJobResult(
            outcome="infrastructure_failure",
            consumed=SchedulerBudget(tokens=7, jobs=1),
        ),
    )
    second = scheduler.claim("worker")[0]
    scheduler.complete(
        second.lease.lease_id,
        CampaignJobResult(
            outcome="candidate_success",
            consumed=SchedulerBudget(tokens=3, jobs=1),
            metadata={"score": 0.5},
        ),
    )

    report = _campaign_mode_report(plan, scheduler, tmp_path / "scheduler-report.json")

    assert report.branches[0].usage.total_tokens == 10
    assert report.branches[0].usage.evaluations == 2
    assert report.branches[0].terminal_state == "succeeded"
