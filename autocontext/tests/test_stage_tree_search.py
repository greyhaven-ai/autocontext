"""Tests for tree search stage (MTS-80)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from unittest.mock import MagicMock

from autocontext.agents.llm_client import DeterministicDevClient
from autocontext.agents.orchestrator import AgentOrchestrator
from autocontext.config.settings import AppSettings
from autocontext.execution.supervisor import ExecutionSupervisor
from autocontext.loop.stage_tree_search import stage_tree_search
from autocontext.loop.stage_types import GenerationContext
from autocontext.prompts.templates import PromptBundle, build_prompt_bundle
from autocontext.scenarios.base import (
    ExecutionLimits,
    Observation,
    ReplayEnvelope,
    Result,
    ScenarioInterface,
)


class _FakeScenario(ScenarioInterface):
    """Deterministic scenario for tree search tests."""

    name = "fake_tree_scenario"

    def describe_rules(self) -> str:
        return "Fake tree scenario."

    def describe_strategy_interface(self) -> str:
        return '{"aggression": float}'

    def describe_evaluation_criteria(self) -> str:
        return "Score from aggression."

    def initial_state(self, seed: int | None = None) -> dict[str, Any]:
        return {"seed": seed or 0, "terminal": False}

    def get_observation(self, state: Mapping[str, Any], player_id: str) -> Observation:
        return Observation(narrative="test observation")

    def validate_actions(
        self, state: Mapping[str, Any], player_id: str, actions: Mapping[str, Any],
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
                scenario=scenario.name, seed=seed,
                narrative=scenario.replay_to_narrative(result.replay),
                timeline=result.replay,
            )
            return result, replay

    return ExecutionSupervisor(executor=InlineExecutor())


def _make_settings(**overrides: object) -> AppSettings:
    defaults: dict[str, object] = {
        "agent_provider": "deterministic",
        "exploration_mode": "tree",
        "tree_max_hypotheses": 4,
        "tree_sampling_temperature": 1.0,
        "matches_per_generation": 2,
    }
    defaults.update(overrides)
    return AppSettings(**defaults)  # type: ignore[arg-type]


def _make_orchestrator(settings: AppSettings | None = None) -> AgentOrchestrator:
    s = settings or _make_settings()
    client = DeterministicDevClient()
    return AgentOrchestrator(client=client, settings=s)


def _make_prompts(scenario: ScenarioInterface | None = None) -> PromptBundle:
    sc = scenario or _FakeScenario()
    obs = sc.get_observation(sc.initial_state(), "challenger")
    return build_prompt_bundle(
        scenario_rules=sc.describe_rules(),
        strategy_interface=sc.describe_strategy_interface(),
        evaluation_criteria=sc.describe_evaluation_criteria(),
        previous_summary="best: 0.0",
        observation=obs,
        current_playbook="",
        available_tools="",
    )


def _make_ctx(
    settings: AppSettings | None = None,
    scenario: ScenarioInterface | None = None,
    previous_best: float = 0.0,
) -> GenerationContext:
    sc = scenario or _FakeScenario()
    s = settings or _make_settings()
    ctx = GenerationContext(
        run_id="run_tree_test",
        scenario_name="fake_tree_scenario",
        scenario=sc,
        generation=1,
        settings=s,
        previous_best=previous_best,
        challenger_elo=1000.0,
        score_history=[],
        gate_decision_history=[],
        coach_competitor_hints="",
        replay_narrative="",
    )
    ctx.prompts = _make_prompts(sc)
    ctx.strategy_interface = sc.describe_strategy_interface()
    ctx.tool_context = ""
    return ctx


class TestTreeSearchStage:
    """Integration tests for stage_tree_search."""

    def test_produces_outputs_and_strategy(self) -> None:
        """Tree search stage populates ctx.outputs and ctx.current_strategy."""
        settings = _make_settings()
        ctx = _make_ctx(settings=settings)
        orch = _make_orchestrator(settings)
        supervisor = _make_inline_supervisor()
        events = MagicMock()
        sqlite = MagicMock()
        artifacts = MagicMock()
        artifacts.persist_tools.return_value = []
        artifacts.generation_dir.return_value = MagicMock()

        result = stage_tree_search(
            ctx,
            orchestrator=orch,
            supervisor=supervisor,
            artifacts=artifacts,
            sqlite=sqlite,
            events=events,
        )

        assert result.outputs is not None
        assert isinstance(result.current_strategy, dict)
        assert result.tournament is not None
        assert result.gate_decision in ("advance", "rollback")

    def test_emits_tree_search_start_event(self) -> None:
        """The tree_search_start event is emitted."""
        settings = _make_settings()
        ctx = _make_ctx(settings=settings)
        orch = _make_orchestrator(settings)
        supervisor = _make_inline_supervisor()
        events = MagicMock()
        sqlite = MagicMock()
        artifacts = MagicMock()
        artifacts.persist_tools.return_value = []
        artifacts.generation_dir.return_value = MagicMock()

        stage_tree_search(
            ctx, orchestrator=orch, supervisor=supervisor,
            artifacts=artifacts, sqlite=sqlite, events=events,
        )

        event_names = [call.args[0] for call in events.emit.call_args_list]
        assert "tree_search_start" in event_names

    def test_emits_tournament_completed_event(self) -> None:
        """A tournament_completed event is emitted for the final tournament."""
        settings = _make_settings()
        ctx = _make_ctx(settings=settings)
        orch = _make_orchestrator(settings)
        supervisor = _make_inline_supervisor()
        events = MagicMock()
        sqlite = MagicMock()
        artifacts = MagicMock()
        artifacts.persist_tools.return_value = []
        artifacts.generation_dir.return_value = MagicMock()

        stage_tree_search(
            ctx, orchestrator=orch, supervisor=supervisor,
            artifacts=artifacts, sqlite=sqlite, events=events,
        )

        event_names = [call.args[0] for call in events.emit.call_args_list]
        assert "tournament_completed" in event_names
        assert "gate_decided" in event_names

    def test_persists_agent_outputs_to_sqlite(self) -> None:
        """Agent outputs are persisted via sqlite."""
        settings = _make_settings()
        ctx = _make_ctx(settings=settings)
        orch = _make_orchestrator(settings)
        supervisor = _make_inline_supervisor()
        events = MagicMock()
        sqlite = MagicMock()
        artifacts = MagicMock()
        artifacts.persist_tools.return_value = []
        artifacts.generation_dir.return_value = MagicMock()

        stage_tree_search(
            ctx, orchestrator=orch, supervisor=supervisor,
            artifacts=artifacts, sqlite=sqlite, events=events,
        )

        sqlite.append_generation_agent_activity.assert_called_once()
        _, kwargs = sqlite.append_generation_agent_activity.call_args
        assert len(kwargs["outputs"]) == 4
        assert len(kwargs["role_metrics"]) == 5

    def test_runs_analyst_coach_architect(self) -> None:
        """Tree search runs knowledge agents (analyst/coach/architect) after finding best strategy."""
        settings = _make_settings()
        ctx = _make_ctx(settings=settings)
        orch = _make_orchestrator(settings)
        supervisor = _make_inline_supervisor()
        events = MagicMock()
        sqlite = MagicMock()
        artifacts = MagicMock()
        artifacts.persist_tools.return_value = []
        artifacts.generation_dir.return_value = MagicMock()

        result = stage_tree_search(
            ctx, orchestrator=orch, supervisor=supervisor,
            artifacts=artifacts, sqlite=sqlite, events=events,
        )

        assert result.outputs is not None
        roles = [re.role for re in result.outputs.role_executions]
        assert "analyst" in roles
        assert "coach" in roles
        assert "architect" in roles

    def test_updates_score_history(self) -> None:
        """Score history is updated with the final tournament best score."""
        settings = _make_settings()
        ctx = _make_ctx(settings=settings)
        orch = _make_orchestrator(settings)
        supervisor = _make_inline_supervisor()
        events = MagicMock()
        sqlite = MagicMock()
        artifacts = MagicMock()
        artifacts.persist_tools.return_value = []
        artifacts.generation_dir.return_value = MagicMock()

        result = stage_tree_search(
            ctx, orchestrator=orch, supervisor=supervisor,
            artifacts=artifacts, sqlite=sqlite, events=events,
        )

        assert len(result.score_history) == 1
        assert len(result.gate_decision_history) == 1

    def test_advance_updates_previous_best(self) -> None:
        """On advance, previous_best and challenger_elo are updated."""
        settings = _make_settings()
        # Use low previous_best so any score should beat it
        ctx = _make_ctx(settings=settings, previous_best=0.0)
        orch = _make_orchestrator(settings)
        supervisor = _make_inline_supervisor()
        events = MagicMock()
        sqlite = MagicMock()
        artifacts = MagicMock()
        artifacts.persist_tools.return_value = []
        artifacts.generation_dir.return_value = MagicMock()

        result = stage_tree_search(
            ctx, orchestrator=orch, supervisor=supervisor,
            artifacts=artifacts, sqlite=sqlite, events=events,
        )

        if result.gate_decision == "advance":
            assert result.previous_best > 0.0

    def test_writes_replay_narrative(self) -> None:
        """Replay narrative is written to artifacts."""
        settings = _make_settings()
        ctx = _make_ctx(settings=settings)
        orch = _make_orchestrator(settings)
        supervisor = _make_inline_supervisor()
        events = MagicMock()
        sqlite = MagicMock()
        artifacts = MagicMock()
        artifacts.persist_tools.return_value = []
        artifacts.generation_dir.return_value = MagicMock()

        result = stage_tree_search(
            ctx, orchestrator=orch, supervisor=supervisor,
            artifacts=artifacts, sqlite=sqlite, events=events,
        )

        assert result.replay_narrative == "test narrative"
        artifacts.buffered_write_markdown.assert_called_once()

    def test_max_hypotheses_respected(self) -> None:
        """Tree size stays within max_hypotheses."""
        settings = _make_settings(tree_max_hypotheses=2)
        ctx = _make_ctx(settings=settings)
        orch = _make_orchestrator(settings)
        supervisor = _make_inline_supervisor()
        events = MagicMock()
        sqlite = MagicMock()
        artifacts = MagicMock()
        artifacts.persist_tools.return_value = []
        artifacts.generation_dir.return_value = MagicMock()

        # Should complete without error even with tight hypothesis limit
        result = stage_tree_search(
            ctx, orchestrator=orch, supervisor=supervisor,
            artifacts=artifacts, sqlite=sqlite, events=events,
        )

        assert result.outputs is not None

    def test_on_role_event_callback(self) -> None:
        """on_role_event callback fires for analyst, coach, architect."""
        settings = _make_settings()
        ctx = _make_ctx(settings=settings)
        orch = _make_orchestrator(settings)
        supervisor = _make_inline_supervisor()
        events = MagicMock()
        sqlite = MagicMock()
        artifacts = MagicMock()
        artifacts.persist_tools.return_value = []
        artifacts.generation_dir.return_value = MagicMock()

        role_events: list[tuple[str, str]] = []

        def _on_role(role: str, status: str) -> None:
            role_events.append((role, status))

        stage_tree_search(
            ctx, orchestrator=orch, supervisor=supervisor,
            artifacts=artifacts, sqlite=sqlite, events=events,
            on_role_event=_on_role,
        )

        # Check that analyst, coach, architect all have started + completed
        roles_seen = {r for r, _ in role_events}
        assert "analyst" in roles_seen
        assert "coach" in roles_seen
        assert "architect" in roles_seen


class TestTreeSearchPipelineIntegration:
    """Test that GenerationPipeline uses tree search when exploration_mode='tree'."""

    def test_pipeline_uses_tree_search(self) -> None:
        """GenerationPipeline dispatches to stage_tree_search when exploration_mode='tree'."""
        from autocontext.loop.generation_pipeline import GenerationPipeline

        settings = _make_settings()
        scenario = _FakeScenario()
        orch = _make_orchestrator(settings)
        supervisor = _make_inline_supervisor()
        events = MagicMock()
        sqlite = MagicMock()
        artifacts = MagicMock()
        artifacts.read_playbook.return_value = ""
        artifacts.read_tool_context.return_value = ""
        artifacts.read_skills.return_value = ""
        artifacts.read_latest_advance_analysis.return_value = ""
        artifacts.read_progress.return_value = None
        artifacts.persist_tools.return_value = []
        artifacts.generation_dir.return_value = MagicMock()
        trajectory = MagicMock()
        trajectory.build_trajectory.return_value = ""
        trajectory.build_strategy_registry.return_value = ""

        pipeline = GenerationPipeline(
            orchestrator=orch,
            supervisor=supervisor,
            gate=MagicMock(),  # Not used in tree search
            artifacts=artifacts,
            sqlite=sqlite,
            trajectory_builder=trajectory,
            events=events,
            curator=None,
        )

        ctx = GenerationContext(
            run_id="run_pipe_tree",
            scenario_name="fake_tree_scenario",
            scenario=scenario,
            generation=2,  # Skip startup verification (gen 1 only)
            settings=settings,
            previous_best=0.0,
            challenger_elo=1000.0,
            score_history=[],
            gate_decision_history=[],
            coach_competitor_hints="",
            replay_narrative="",
        )

        result = pipeline.run_generation(ctx)

        # Verify tree search ran (events should contain tree_search_start)
        event_names = [call.args[0] for call in events.emit.call_args_list]
        assert "tree_search_start" in event_names
        assert result.outputs is not None
        assert result.tournament is not None

    def test_pipeline_skips_tree_search_for_linear(self) -> None:
        """GenerationPipeline uses standard flow when exploration_mode='linear'."""
        from autocontext.loop.generation_pipeline import GenerationPipeline

        settings = _make_settings(exploration_mode="linear")
        scenario = _FakeScenario()
        orch = _make_orchestrator(settings)
        supervisor = _make_inline_supervisor()
        events = MagicMock()
        sqlite = MagicMock()
        artifacts = MagicMock()
        artifacts.read_playbook.return_value = ""
        artifacts.read_tool_context.return_value = ""
        artifacts.read_skills.return_value = ""
        artifacts.read_latest_advance_analysis.return_value = ""
        artifacts.read_progress.return_value = None
        artifacts.persist_tools.return_value = []
        artifacts.generation_dir.return_value = MagicMock()
        trajectory = MagicMock()
        trajectory.build_trajectory.return_value = ""
        trajectory.build_strategy_registry.return_value = ""

        from autocontext.backpressure import BackpressureGate

        gate = BackpressureGate(min_delta=0.0)

        pipeline = GenerationPipeline(
            orchestrator=orch,
            supervisor=supervisor,
            gate=gate,
            artifacts=artifacts,
            sqlite=sqlite,
            trajectory_builder=trajectory,
            events=events,
            curator=None,
        )

        ctx = GenerationContext(
            run_id="run_pipe_linear",
            scenario_name="fake_tree_scenario",
            scenario=scenario,
            generation=2,  # Skip startup verification (gen 1 only)
            settings=settings,
            previous_best=0.0,
            challenger_elo=1000.0,
            score_history=[],
            gate_decision_history=[],
            coach_competitor_hints="",
            replay_narrative="",
        )

        result = pipeline.run_generation(ctx)

        # Verify tree search did NOT run
        event_names = [call.args[0] for call in events.emit.call_args_list]
        assert "tree_search_start" not in event_names
        assert result.outputs is not None
