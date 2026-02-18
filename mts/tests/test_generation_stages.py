"""Tests for GenerationContext and StageResult pipeline types."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from unittest.mock import MagicMock

import pytest

from mts.agents.llm_client import DeterministicDevClient
from mts.agents.orchestrator import AgentOrchestrator
from mts.agents.types import AgentOutputs
from mts.config.settings import AppSettings
from mts.execution.supervisor import ExecutionSupervisor
from mts.execution.tournament import TournamentRunner
from mts.loop.stage_types import GenerationContext, StageResult
from mts.loop.stages import (
    stage_agent_generation,
    stage_curator_gate,
    stage_knowledge_setup,
    stage_tournament,
)
from mts.scenarios.base import (
    ExecutionLimits,
    Observation,
    ReplayEnvelope,
    Result,
    ScenarioInterface,
)


def _make_context(**overrides: object) -> GenerationContext:
    """Build a GenerationContext with sensible defaults, overridable per-test."""
    defaults: dict[str, object] = {
        "run_id": "test_run_001",
        "scenario_name": "grid_ctf",
        "scenario": MagicMock(),
        "generation": 1,
        "settings": AppSettings(),
        "previous_best": 0.0,
        "challenger_elo": 1000.0,
        "score_history": [],
        "gate_decision_history": [],
        "coach_competitor_hints": "",
        "replay_narrative": "",
    }
    defaults.update(overrides)
    return GenerationContext(**defaults)  # type: ignore[arg-type]


# ---------- TestGenerationContext ----------


class TestGenerationContext:
    def test_construction_with_required_fields(self) -> None:
        """All required fields are set and accessible after construction."""
        scenario = MagicMock()
        settings = AppSettings()
        ctx = GenerationContext(
            run_id="run_42",
            scenario_name="othello",
            scenario=scenario,
            generation=3,
            settings=settings,
            previous_best=0.65,
            challenger_elo=1050.0,
            score_history=[0.5, 0.6],
            gate_decision_history=["advance"],
            coach_competitor_hints="try aggression=0.7",
            replay_narrative="Player captured flag at step 4",
        )
        assert ctx.run_id == "run_42"
        assert ctx.scenario_name == "othello"
        assert ctx.scenario is scenario
        assert ctx.generation == 3
        assert ctx.settings is settings
        assert ctx.previous_best == 0.65
        assert ctx.challenger_elo == 1050.0
        assert ctx.score_history == [0.5, 0.6]
        assert ctx.gate_decision_history == ["advance"]
        assert ctx.coach_competitor_hints == "try aggression=0.7"
        assert ctx.replay_narrative == "Player captured flag at step 4"

    def test_optional_fields_default_none(self) -> None:
        """Stage output fields default to None, empty string, zero, or empty containers."""
        ctx = _make_context()
        assert ctx.prompts is None
        assert ctx.outputs is None
        assert ctx.tournament is None
        assert ctx.gate_decision == ""
        assert ctx.gate_delta == 0.0
        assert ctx.current_strategy == {}
        assert ctx.created_tools == []
        assert ctx.strategy_interface == ""
        assert ctx.tool_context == ""

    def test_mutable_fields_independent(self) -> None:
        """Two independently constructed contexts do not share list or dict instances."""
        ctx_a = _make_context()
        ctx_b = _make_context()

        # Mutate ctx_a's mutable defaults
        ctx_a.current_strategy["aggression"] = 0.9
        ctx_a.created_tools.append("recon_tool.py")

        # ctx_b must remain unaffected
        assert ctx_b.current_strategy == {}
        assert ctx_b.created_tools == []

        # Also check the required-field lists passed via factory
        ctx_c = _make_context(score_history=[0.5])
        ctx_d = _make_context(score_history=[0.5])
        ctx_c.score_history.append(0.8)
        assert ctx_d.score_history == [0.5]


# ---------- TestStageResult ----------


class TestStageResult:
    def test_success_construction(self) -> None:
        """A successful StageResult has stage name, success=True, and no error."""
        result = StageResult(stage="prompt_assembly", success=True)
        assert result.stage == "prompt_assembly"
        assert result.success is True
        assert result.error is None

    def test_failure_with_error(self) -> None:
        """A failed StageResult carries an error message."""
        result = StageResult(stage="tournament", success=False, error="Timeout after 30s")
        assert result.stage == "tournament"
        assert result.success is False
        assert result.error == "Timeout after 30s"


# ---------- Helpers for stage tests ----------


def _make_settings() -> AppSettings:
    return AppSettings(agent_provider="deterministic")


def _make_scenario_mock() -> MagicMock:
    scenario = MagicMock()
    scenario.name = "test_scenario"
    scenario.describe_rules.return_value = "Test rules"
    scenario.describe_strategy_interface.return_value = '{"aggression": float}'
    scenario.describe_evaluation_criteria.return_value = "Score"
    scenario.initial_state.return_value = {"seed": 1001}
    obs = MagicMock()
    obs.narrative = "Test observation"
    obs.state = {}
    obs.constraints = []
    scenario.get_observation.return_value = obs
    scenario.validate_actions.return_value = (True, "")
    return scenario


def _make_ctx(settings: AppSettings | None = None, scenario: MagicMock | None = None) -> GenerationContext:
    return GenerationContext(
        run_id="run_test",
        scenario_name="test_scenario",
        scenario=scenario or _make_scenario_mock(),
        generation=1,
        settings=settings or _make_settings(),
        previous_best=0.0,
        challenger_elo=1000.0,
        score_history=[],
        gate_decision_history=[],
        coach_competitor_hints="",
        replay_narrative="",
    )


# ---------- TestStageKnowledgeSetup ----------


class TestStageKnowledgeSetup:
    def test_populates_prompts(self) -> None:
        artifacts = MagicMock()
        artifacts.read_playbook.return_value = "Playbook content"
        artifacts.read_tool_context.return_value = ""
        artifacts.read_skills.return_value = ""
        artifacts.read_latest_advance_analysis.return_value = ""
        trajectory = MagicMock()
        trajectory.build_trajectory.return_value = ""
        trajectory.build_strategy_registry.return_value = ""
        ctx = _make_ctx()
        result = stage_knowledge_setup(ctx, artifacts=artifacts, trajectory_builder=trajectory)
        assert result.prompts is not None
        assert result.prompts.competitor  # non-empty

    def test_sets_strategy_interface(self) -> None:
        artifacts = MagicMock()
        artifacts.read_playbook.return_value = ""
        artifacts.read_tool_context.return_value = ""
        artifacts.read_skills.return_value = ""
        artifacts.read_latest_advance_analysis.return_value = ""
        trajectory = MagicMock()
        trajectory.build_trajectory.return_value = ""
        trajectory.build_strategy_registry.return_value = ""
        ctx = _make_ctx()
        result = stage_knowledge_setup(ctx, artifacts=artifacts, trajectory_builder=trajectory)
        assert result.strategy_interface == '{"aggression": float}'

    def test_ablation_skips_knowledge(self) -> None:
        settings = AppSettings(agent_provider="deterministic", ablation_no_feedback=True)
        artifacts = MagicMock()
        trajectory = MagicMock()
        ctx = _make_ctx(settings=settings)
        result = stage_knowledge_setup(ctx, artifacts=artifacts, trajectory_builder=trajectory)
        assert result.prompts is not None
        artifacts.read_playbook.assert_not_called()
        artifacts.read_tool_context.assert_not_called()


# ---------- TestStageAgentGeneration ----------


class TestStageAgentGeneration:
    def test_populates_outputs_and_strategy(self) -> None:
        settings = _make_settings()
        client = DeterministicDevClient()
        orch = AgentOrchestrator(client=client, settings=settings)
        scenario = _make_scenario_mock()
        ctx = _make_ctx(settings=settings, scenario=scenario)

        # Simulate stage 1 ran
        from mts.prompts.templates import build_prompt_bundle

        ctx.prompts = build_prompt_bundle(
            scenario_rules="Test",
            strategy_interface='{"aggression": float}',
            evaluation_criteria="Score",
            previous_summary="best: 0.0",
            observation=scenario.get_observation(None, "challenger"),
            current_playbook="",
            available_tools="",
        )
        ctx.strategy_interface = '{"aggression": float}'

        artifacts = MagicMock()
        artifacts.persist_tools.return_value = ["tool1.py"]
        sqlite = MagicMock()

        result = stage_agent_generation(ctx, orchestrator=orch, artifacts=artifacts, sqlite=sqlite)
        assert result.outputs is not None
        assert len(result.outputs.role_executions) == 5
        assert isinstance(result.current_strategy, dict)
        assert result.created_tools == ["tool1.py"]

    def test_raises_on_invalid_strategy(self) -> None:
        settings = _make_settings()
        client = DeterministicDevClient()
        orch = AgentOrchestrator(client=client, settings=settings)
        scenario = _make_scenario_mock()
        scenario.validate_actions.return_value = (False, "bad strategy")
        ctx = _make_ctx(settings=settings, scenario=scenario)

        from mts.prompts.templates import build_prompt_bundle

        ctx.prompts = build_prompt_bundle(
            scenario_rules="Test",
            strategy_interface='{"aggression": float}',
            evaluation_criteria="Score",
            previous_summary="best: 0.0",
            observation=scenario.get_observation(None, "challenger"),
            current_playbook="",
            available_tools="",
        )
        ctx.strategy_interface = '{"aggression": float}'

        artifacts = MagicMock()
        sqlite = MagicMock()

        with pytest.raises(ValueError, match="competitor strategy validation failed"):
            stage_agent_generation(ctx, orchestrator=orch, artifacts=artifacts, sqlite=sqlite)

    def test_persists_agent_outputs(self) -> None:
        settings = _make_settings()
        client = DeterministicDevClient()
        orch = AgentOrchestrator(client=client, settings=settings)
        scenario = _make_scenario_mock()
        ctx = _make_ctx(settings=settings, scenario=scenario)

        from mts.prompts.templates import build_prompt_bundle

        ctx.prompts = build_prompt_bundle(
            scenario_rules="Test",
            strategy_interface='{"aggression": float}',
            evaluation_criteria="Score",
            previous_summary="best: 0.0",
            observation=scenario.get_observation(None, "challenger"),
            current_playbook="",
            available_tools="",
        )
        ctx.strategy_interface = '{"aggression": float}'

        artifacts = MagicMock()
        artifacts.persist_tools.return_value = []
        sqlite = MagicMock()

        stage_agent_generation(ctx, orchestrator=orch, artifacts=artifacts, sqlite=sqlite)

        # Should have called append_agent_output 4 times (competitor, analyst, coach, architect)
        assert sqlite.append_agent_output.call_count == 4
        # Should have called append_agent_role_metric 5 times (all role_executions)
        assert sqlite.append_agent_role_metric.call_count == 5


# ---------- Helpers for tournament / curator stage tests ----------


class _FakeScenario(ScenarioInterface):
    """Deterministic scenario for tournament stage tests."""

    name = "fake_scenario"

    def describe_rules(self) -> str:
        return "Fake scenario for testing."

    def describe_strategy_interface(self) -> str:
        return '{"aggression": float}'

    def describe_evaluation_criteria(self) -> str:
        return "Score is derived from aggression parameter."

    def initial_state(self, seed: int | None = None) -> dict[str, Any]:
        return {"seed": seed or 0, "terminal": False}

    def get_observation(self, state: Mapping[str, Any], player_id: str) -> Observation:
        return Observation(narrative="test observation")

    def validate_actions(
        self, state: Mapping[str, Any], player_id: str, actions: Mapping[str, Any]
    ) -> tuple[bool, str]:
        return (True, "")

    def step(self, state: Mapping[str, Any], actions: Mapping[str, Any]) -> dict[str, Any]:
        aggression = float(actions.get("aggression", 0.5))
        seed = state.get("seed", 0)
        score = min(1.0, aggression * (1 + seed % 5) / 5)
        return {"seed": seed, "terminal": True, "score": score}

    def is_terminal(self, state: Mapping[str, Any]) -> bool:
        return state.get("terminal", False)

    def get_result(self, state: Mapping[str, Any]) -> Result:
        score = state.get("score", 0.5)
        return Result(score=score, summary="test", replay=[])

    def replay_to_narrative(self, replay: list[dict[str, Any]]) -> str:
        return "test narrative"

    def render_frame(self, state: Mapping[str, Any]) -> dict[str, Any]:
        return {"state": dict(state)}


def _make_inline_supervisor() -> ExecutionSupervisor:
    """Create a supervisor using a simple inline executor wrapping execute_match."""

    class InlineExecutor:
        def execute(
            self,
            scenario: ScenarioInterface,
            strategy: object,
            seed: int,
            limits: ExecutionLimits,
        ) -> tuple[object, ReplayEnvelope]:
            result = scenario.execute_match(strategy=strategy, seed=seed)
            replay = ReplayEnvelope(
                scenario=scenario.name,
                seed=seed,
                narrative=scenario.replay_to_narrative(result.replay),
                timeline=result.replay,
            )
            return result, replay

    return ExecutionSupervisor(executor=InlineExecutor())


def _make_tournament_ctx(
    scenario: ScenarioInterface | None = None,
    strategy: dict | None = None,
    previous_best: float = 0.0,
    settings: AppSettings | None = None,
) -> GenerationContext:
    """Build a GenerationContext wired to FakeScenario for tournament tests."""
    sc = scenario or _FakeScenario()
    stg = strategy or {"aggression": 0.8}
    return GenerationContext(
        run_id="run_tourn",
        scenario_name="fake_scenario",
        scenario=sc,
        generation=1,
        settings=settings or _make_settings(),
        previous_best=previous_best,
        challenger_elo=1000.0,
        score_history=[],
        gate_decision_history=[],
        coach_competitor_hints="",
        replay_narrative="",
        current_strategy=stg,
        outputs=MagicMock(spec=AgentOutputs, strategy=stg),
    )


# ---------- TestStageTournament ----------


class TestStageTournament:
    def _run(
        self,
        ctx: GenerationContext | None = None,
        gate_decision: str = "advance",
        gate_reason: str = "improved",
    ) -> GenerationContext:
        ctx = ctx or _make_tournament_ctx()
        supervisor = _make_inline_supervisor()
        tournament_runner = TournamentRunner(supervisor=supervisor)
        gate = MagicMock()
        gate.evaluate.return_value = MagicMock(decision=gate_decision, reason=gate_reason)
        events = MagicMock()
        sqlite = MagicMock()
        artifacts = MagicMock()
        return stage_tournament(
            ctx,
            tournament_runner=tournament_runner,
            gate=gate,
            events=events,
            sqlite=sqlite,
            artifacts=artifacts,
            agents=None,
        )

    def test_populates_tournament_and_gate(self) -> None:
        """After stage_tournament, ctx.tournament is set and gate_decision is populated."""
        ctx = self._run(gate_decision="advance")
        assert ctx.tournament is not None
        assert ctx.gate_decision == "advance"
        assert ctx.tournament.mean_score > 0
        assert ctx.tournament.best_score > 0

    def test_advance_updates_previous_best(self) -> None:
        """On advance, previous_best is updated to tournament best score."""
        ctx = _make_tournament_ctx(previous_best=0.0)
        result = self._run(ctx=ctx, gate_decision="advance")
        # FakeScenario with aggression=0.8 produces scores > 0 for most seeds
        assert result.previous_best > 0.0

    def test_rollback_does_not_update_best(self) -> None:
        """On rollback, previous_best remains unchanged."""
        original_best = 0.5
        ctx = _make_tournament_ctx(previous_best=original_best)
        result = self._run(ctx=ctx, gate_decision="rollback")
        assert result.previous_best == original_best

    def test_accumulates_score_history(self) -> None:
        """score_history and gate_decision_history get appended after tournament."""
        ctx = _make_tournament_ctx()
        assert len(ctx.score_history) == 0
        assert len(ctx.gate_decision_history) == 0
        result = self._run(ctx=ctx, gate_decision="advance")
        assert len(result.score_history) == 1
        assert result.score_history[0] > 0
        assert result.gate_decision_history == ["advance"]

    def test_builds_replay_narrative(self) -> None:
        """After tournament, replay_narrative is populated from the best match."""
        ctx = _make_tournament_ctx()
        result = self._run(ctx=ctx)
        assert result.replay_narrative  # non-empty
        assert isinstance(result.replay_narrative, str)


# ---------- TestStageCuratorGate ----------


def _make_curator_ctx(
    gate_decision: str = "advance",
    coach_playbook: str = "some playbook content",
    ablation: bool = False,
) -> GenerationContext:
    """Build a GenerationContext wired for curator gate tests."""
    settings = AppSettings(agent_provider="deterministic", ablation_no_feedback=ablation)
    outputs = MagicMock(spec=AgentOutputs)
    outputs.coach_playbook = coach_playbook
    return GenerationContext(
        run_id="run_curator",
        scenario_name="test_scenario",
        scenario=MagicMock(),
        generation=1,
        settings=settings,
        previous_best=0.5,
        challenger_elo=1020.0,
        score_history=[0.5],
        gate_decision_history=["advance"],
        coach_competitor_hints="",
        replay_narrative="narrative",
        gate_decision=gate_decision,
        outputs=outputs,
    )


class TestStageCuratorGate:
    def test_noop_when_no_curator(self) -> None:
        """When curator=None, outputs remain unchanged."""
        ctx = _make_curator_ctx(gate_decision="advance", coach_playbook="my playbook")
        original_playbook = ctx.outputs.coach_playbook
        result = stage_curator_gate(
            ctx,
            curator=None,
            artifacts=MagicMock(),
            trajectory_builder=MagicMock(),
            sqlite=MagicMock(),
            events=MagicMock(),
        )
        assert result.outputs.coach_playbook == original_playbook

    def test_noop_when_not_advance(self) -> None:
        """When gate_decision is not 'advance', curator is not called."""
        curator = MagicMock()
        ctx = _make_curator_ctx(gate_decision="rollback")
        stage_curator_gate(
            ctx,
            curator=curator,
            artifacts=MagicMock(),
            trajectory_builder=MagicMock(),
            sqlite=MagicMock(),
            events=MagicMock(),
        )
        curator.assess_playbook_quality.assert_not_called()

    def test_noop_when_no_coach_playbook(self) -> None:
        """When coach_playbook is empty, curator is not called."""
        curator = MagicMock()
        ctx = _make_curator_ctx(gate_decision="advance", coach_playbook="")
        stage_curator_gate(
            ctx,
            curator=curator,
            artifacts=MagicMock(),
            trajectory_builder=MagicMock(),
            sqlite=MagicMock(),
            events=MagicMock(),
        )
        curator.assess_playbook_quality.assert_not_called()

    def test_noop_when_ablation(self) -> None:
        """When ablation_no_feedback is True, curator is not called."""
        curator = MagicMock()
        ctx = _make_curator_ctx(gate_decision="advance", ablation=True)
        stage_curator_gate(
            ctx,
            curator=curator,
            artifacts=MagicMock(),
            trajectory_builder=MagicMock(),
            sqlite=MagicMock(),
            events=MagicMock(),
        )
        curator.assess_playbook_quality.assert_not_called()
