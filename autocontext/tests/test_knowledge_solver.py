from __future__ import annotations

import json
from pathlib import Path

import pytest

from autocontext.agents.llm_client import DeterministicDevClient
from autocontext.agents.subagent_runtime import SubagentRuntime
from autocontext.config.settings import AppSettings
from autocontext.knowledge.export import export_skill_package
from autocontext.mcp.tools import MtsToolContext
from autocontext.scenarios import SCENARIO_REGISTRY
from autocontext.scenarios.agent_task import AgentTaskInterface, AgentTaskResult
from autocontext.scenarios.artifact_editing import (
    Artifact,
    ArtifactEditingInterface,
    ArtifactEditingResult,
    ArtifactValidationResult,
)
from autocontext.scenarios.custom.operator_loop_designer import OPERATOR_LOOP_SPEC_END, OPERATOR_LOOP_SPEC_START
from autocontext.scenarios.families import detect_family
from autocontext.storage.sqlite_store import SQLiteStore


def _operator_loop_llm(system: str, user: str) -> str:
    del system, user
    spec = {
        "description": "A support queue where high-risk actions require operator escalation.",
        "environment_description": "The agent triages support requests and can defer risky actions to a human operator.",
        "initial_state_description": "A new request is waiting for triage.",
        "escalation_policy": {"escalation_threshold": "high", "max_escalations": 2},
        "success_criteria": [
            "routine issues are handled safely",
            "high-risk actions are escalated to a human operator",
        ],
        "failure_modes": ["unsafe autonomous handling"],
        "max_steps": 5,
        "actions": [
            {
                "name": "review_request",
                "description": "Assess the request and available evidence.",
                "parameters": {},
                "preconditions": [],
                "effects": ["request_reviewed"],
            },
            {
                "name": "escalate_to_human_operator",
                "description": "Escalate the risky request to a human operator.",
                "parameters": {},
                "preconditions": ["review_request"],
                "effects": ["operator_guidance_available"],
            },
            {
                "name": "continue_with_operator_guidance",
                "description": "Resume handling after operator guidance is received.",
                "parameters": {},
                "preconditions": ["escalate_to_human_operator"],
                "effects": ["request_resolved"],
            },
        ],
    }
    return f"{OPERATOR_LOOP_SPEC_START}\n{json.dumps(spec)}\n{OPERATOR_LOOP_SPEC_END}"


class _StubProviderResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class _StubProvider:
    def __init__(self, text: str) -> None:
        self._text = text

    def complete(self, system_prompt: str, user_prompt: str, model: str = "") -> _StubProviderResponse:
        del system_prompt, user_prompt, model
        return _StubProviderResponse(self._text)

    def default_model(self) -> str:
        return "test-model"


class _SolveAgentTask(AgentTaskInterface):
    name = "solve_agent_task_fixture"

    def get_task_prompt(self, state: dict) -> str:
        del state
        return "Reply with exactly: improved draft"

    def evaluate_output(self, output: str, state: dict, **kwargs: object) -> AgentTaskResult:
        del state, kwargs
        score = 1.0 if output.strip() == "improved draft" else 0.2
        return AgentTaskResult(
            score=score,
            reasoning="matched expected task output" if score == 1.0 else "output mismatch",
            dimension_scores={"quality": score},
        )

    def get_rubric(self) -> str:
        return "Score exact_match 0-1."

    def initial_state(self, seed: int | None = None) -> dict:
        del seed
        return {}

    def describe_task(self) -> str:
        return "Return the expected draft text."


class _SolveArtifactEditing(ArtifactEditingInterface):
    name = "solve_artifact_editing_fixture"

    def describe_task(self) -> str:
        return "Update the YAML artifact so foo is set to new."

    def get_rubric(self) -> str:
        return "Reward valid edits that change only the target field."

    def initial_artifacts(self, seed: int | None = None) -> list[Artifact]:
        del seed
        return [Artifact(path="config.yaml", content="foo: old\n", content_type="yaml", metadata={})]

    def get_edit_prompt(self, artifacts: list[Artifact]) -> str:
        del artifacts
        return "Return JSON with an artifacts array containing the full edited artifact set."

    def validate_artifact(self, artifact: Artifact) -> ArtifactValidationResult:
        errors = [] if artifact.content.strip() == "foo: new" else ["config.yaml did not update foo"]
        return ArtifactValidationResult(valid=not errors, errors=errors, warnings=[])

    def evaluate_edits(self, original: list[Artifact], edited: list[Artifact]) -> ArtifactEditingResult:
        del original
        validation = self.validate_artifact(edited[0])
        score = 1.0 if validation.valid else 0.0
        return ArtifactEditingResult(
            score=score,
            reasoning="artifact updated" if validation.valid else "artifact invalid",
            dimension_scores={"correctness": score},
            diffs=self.compute_diffs(self.initial_artifacts(), edited),
            validation=validation,
            artifacts_modified=1 if score else 0,
            artifacts_valid=1 if score else 0,
        )


class _BudgetExhaustingAgentTask(AgentTaskInterface):
    name = "solve_budget_exhausting_fixture"

    def __init__(self, clock: dict[str, float]) -> None:
        self._clock = clock

    def get_task_prompt(self, state: dict) -> str:
        del state
        return "Reply with exactly: improved draft"

    def evaluate_output(self, output: str, state: dict, **kwargs: object) -> AgentTaskResult:
        del output, state, kwargs
        self._clock["now"] = 2.0
        return AgentTaskResult(
            score=1.0,
            reasoning="budget should expire after evaluation",
            dimension_scores={"quality": 1.0},
        )

    def get_rubric(self) -> str:
        return "Score exact_match 0-1."

    def initial_state(self, seed: int | None = None) -> dict:
        del seed
        return {}

    def describe_task(self) -> str:
        return "Return the expected draft text."


class TestSolveScenarioBuilder:
    def test_routes_operator_loop_descriptions_to_operator_loop_creator(self, tmp_path: Path) -> None:
        from autocontext.knowledge.solver import SolveScenarioBuilder

        runtime = SubagentRuntime(DeterministicDevClient())
        builder = SolveScenarioBuilder(
            runtime=runtime,
            llm_fn=_operator_loop_llm,
            model="test-model",
            knowledge_root=tmp_path,
        )

        result = builder.build(
            "Create and solve an operator-loop escalation scenario for an autonomous support agent "
            "that escalates high-risk account actions to a human operator."
        )

        scenario_dir = tmp_path / "_custom_scenarios" / result.scenario_name
        spec_payload = json.loads((scenario_dir / "spec.json").read_text(encoding="utf-8"))
        scenario = SCENARIO_REGISTRY[result.scenario_name]()

        assert result.family_name == "operator_loop"
        assert spec_payload["scenario_type"] == "operator_loop"
        assert detect_family(scenario).name == "operator_loop"

    def test_keeps_legacy_game_creator_for_game_descriptions(self, tmp_path: Path) -> None:
        from autocontext.knowledge.solver import SolveScenarioBuilder

        runtime = SubagentRuntime(DeterministicDevClient())
        builder = SolveScenarioBuilder(
            runtime=runtime,
            llm_fn=_operator_loop_llm,
            model="test-model",
            knowledge_root=tmp_path,
        )

        result = builder.build("Create and solve a resource management game about balancing mining and defense.")

        scenario_dir = tmp_path / "_custom_scenarios" / result.scenario_name
        spec_payload = json.loads((scenario_dir / "spec.json").read_text(encoding="utf-8"))
        scenario = SCENARIO_REGISTRY[result.scenario_name]()

        assert result.family_name == "game"
        assert spec_payload["scenario_type"] == "parametric"
        assert detect_family(scenario).name == "game"

    def test_prefers_supported_family_hint_from_proposal_metadata(self) -> None:
        from autocontext.knowledge.solver import _resolve_requested_scenario_family

        family = _resolve_requested_scenario_family(
            "Scenario Proposal: peer_review_panel — multi-role adversarial coordination\n\n"
            "## Scenario Proposal\n\n"
            "**Family:** coordination / adversarial_self_play\n"
            "**Priority:** Week 4\n\n"
            "### Description\n\n"
            "Three instances (Author, Critic A, Critic B) collaborate. Author produces artifact, "
            "Critic A finds weaknesses, Critic B defends and challenges objections."
        )

        assert family.name == "coordination"

    def test_passes_supported_family_hint_into_creator(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from autocontext.knowledge.solver import SolveScenarioBuilder

        runtime = SubagentRuntime(DeterministicDevClient())
        builder = SolveScenarioBuilder(
            runtime=runtime,
            llm_fn=_operator_loop_llm,
            model="test-model",
            knowledge_root=tmp_path,
        )
        captured: dict[str, str] = {}

        class _CreatedScenario:
            name = "peer_review_panel_fixture"

        def _fake_create(self, description: str, *, family_name: str = "") -> _CreatedScenario:
            del self, description
            captured["family_name"] = family_name
            return _CreatedScenario()

        monkeypatch.setattr(
            "autocontext.scenarios.custom.agent_task_creator.AgentTaskCreator.create",
            _fake_create,
        )

        result = builder.build(
            "Scenario Proposal: peer_review_panel — multi-role adversarial coordination\n\n"
            "## Scenario Proposal\n\n"
            "**Family:** coordination / adversarial_self_play\n"
            "**Priority:** Week 4\n\n"
            "### Description\n\n"
            "Three instances (Author, Critic A, Critic B) collaborate. Author produces artifact, "
            "Critic A finds weaknesses, Critic B defends and challenges objections."
        )

        assert captured["family_name"] == "coordination"
        assert result.family_name == "coordination"


class TestSolveScenarioExecutor:
    def test_runs_agent_task_scenarios_through_task_loop(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from autocontext.knowledge.solver import SolveScenarioExecutor

        settings = AppSettings(
            knowledge_root=tmp_path / "knowledge",
            db_path=tmp_path / "runs.sqlite3",
        )
        scenario_name = "solve_agent_task_execution"
        previous = SCENARIO_REGISTRY.get(scenario_name)
        SCENARIO_REGISTRY[scenario_name] = _SolveAgentTask
        monkeypatch.setattr(
            "autocontext.knowledge.solver.resolve_role_runtime",
            lambda settings, **kwargs: (_StubProvider("improved draft"), "test-model"),
        )

        try:
            executor = SolveScenarioExecutor(settings)
            summary = executor.execute(
                scenario_name=scenario_name,
                family_name="agent_task",
                generations=1,
            )
            package = export_skill_package(MtsToolContext(settings), scenario_name)
        finally:
            if previous is None:
                SCENARIO_REGISTRY.pop(scenario_name, None)
            else:
                SCENARIO_REGISTRY[scenario_name] = previous

        sqlite = SQLiteStore(settings.db_path)
        sqlite.migrate(Path(__file__).resolve().parents[1] / "migrations")

        assert summary.generations_executed == 1
        assert summary.best_score == 1.0
        assert package.best_score == 1.0
        assert package.metadata["has_snapshot"] is True
        assert sqlite.count_completed_runs(scenario_name) == 1

    def test_artifact_editing_adapter_preserves_omitted_artifact_deletions(self) -> None:
        from autocontext.knowledge.solver import ArtifactEditingTaskAdapter

        adapter = ArtifactEditingTaskAdapter(_SolveArtifactEditing())
        original = [
            Artifact(path="config.yaml", content="foo: old\n", content_type="yaml", metadata={}),
            Artifact(path="legacy.yaml", content="delete: true\n", content_type="yaml", metadata={}),
        ]

        edited = adapter._parse_edited_artifacts(
            json.dumps(
                {
                    "artifacts": [
                        {
                            "path": "config.yaml",
                            "content": "foo: new\n",
                            "content_type": "yaml",
                        }
                    ]
                }
            ),
            original,
        )

        assert [artifact.path for artifact in edited] == ["config.yaml"]

    def test_runs_artifact_editing_scenarios_through_task_loop(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from autocontext.knowledge.solver import SolveScenarioExecutor

        settings = AppSettings(
            knowledge_root=tmp_path / "knowledge",
            db_path=tmp_path / "runs.sqlite3",
        )
        scenario_name = "solve_artifact_editing_execution"
        previous = SCENARIO_REGISTRY.get(scenario_name)
        SCENARIO_REGISTRY[scenario_name] = _SolveArtifactEditing
        monkeypatch.setattr(
            "autocontext.knowledge.solver.resolve_role_runtime",
            lambda settings, **kwargs: (
                _StubProvider(
                    json.dumps(
                        {
                            "artifacts": [
                                {
                                    "path": "config.yaml",
                                    "content": "foo: new\n",
                                    "content_type": "yaml",
                                }
                            ]
                        }
                    )
                ),
                "test-model",
            ),
        )

        try:
            executor = SolveScenarioExecutor(settings)
            summary = executor.execute(
                scenario_name=scenario_name,
                family_name="artifact_editing",
                generations=1,
            )
        finally:
            if previous is None:
                SCENARIO_REGISTRY.pop(scenario_name, None)
            else:
                SCENARIO_REGISTRY[scenario_name] = previous

        sqlite = SQLiteStore(settings.db_path)
        sqlite.migrate(Path(__file__).resolve().parents[1] / "migrations")

        assert summary.generations_executed == 1
        assert summary.best_score == 1.0
        assert sqlite.count_completed_runs(scenario_name) == 1

    def test_task_like_executor_marks_run_failed_when_budget_expires(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from autocontext.knowledge.solver import SolveScenarioExecutor

        clock = {"now": 0.0}
        settings = AppSettings(
            knowledge_root=tmp_path / "knowledge",
            db_path=tmp_path / "runs.sqlite3",
            generation_time_budget_seconds=1,
        )
        scenario_name = "solve_budget_exhausting_execution"
        previous = SCENARIO_REGISTRY.get(scenario_name)
        SCENARIO_REGISTRY[scenario_name] = lambda: _BudgetExhaustingAgentTask(clock)
        monkeypatch.setattr("autocontext.knowledge.solver.time.monotonic", lambda: clock["now"])
        monkeypatch.setattr(
            "autocontext.knowledge.solver.resolve_role_runtime",
            lambda settings, **kwargs: (_StubProvider("improved draft"), "test-model"),
        )

        try:
            executor = SolveScenarioExecutor(settings)
            with pytest.raises(TimeoutError, match="time budget exceeded"):
                executor.execute(
                    scenario_name=scenario_name,
                    family_name="agent_task",
                    generations=1,
                )
        finally:
            if previous is None:
                SCENARIO_REGISTRY.pop(scenario_name, None)
            else:
                SCENARIO_REGISTRY[scenario_name] = previous

        sqlite = SQLiteStore(settings.db_path)
        sqlite.migrate(Path(__file__).resolve().parents[1] / "migrations")

        assert sqlite.count_completed_runs(scenario_name) == 0


class TestSolveManager:
    def test_run_job_uses_family_aware_executor(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from autocontext.knowledge.export import SkillPackage
        from autocontext.knowledge.solver import (
            SolveExecutionSummary,
            SolveJob,
            SolveManager,
            SolveScenarioBuildResult,
        )

        settings = AppSettings(
            knowledge_root=tmp_path / "knowledge",
            db_path=tmp_path / "runs.sqlite3",
        )
        manager = SolveManager(settings)
        created = SolveScenarioBuildResult(
            scenario_name="solve_agent_task_execution",
            family_name="agent_task",
        )

        class _FakeBuilder:
            def build(self, description: str) -> SolveScenarioBuildResult:
                del description
                return created

        fake_package = SkillPackage(
            scenario_name=created.scenario_name,
            display_name="Solve Agent Task Execution",
            description="fixture",
            playbook="",
            lessons=[],
            best_strategy=None,
            best_score=1.0,
            best_elo=1500.0,
            hints="",
            harness={},
        )

        monkeypatch.setattr(manager, "_build_creator", lambda: _FakeBuilder())
        monkeypatch.setattr(
            "autocontext.knowledge.solver.SolveScenarioExecutor.execute",
            lambda self, **kwargs: SolveExecutionSummary(
                run_id="solve_fixture",
                generations_executed=3,
                best_score=0.9,
            ),
        )
        monkeypatch.setattr("autocontext.knowledge.solver.export_skill_package", lambda ctx, name: fake_package)

        job = SolveJob(job_id="solve_fixture_job", description="fixture", generations=3)
        manager._run_job(job)

        assert job.status == "completed"
        assert job.progress == 3
        assert job.result == fake_package
