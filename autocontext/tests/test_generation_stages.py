"""Tests for GenerationContext and StageResult pipeline types."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from autocontext.agents.feedback_loops import AnalystRating, ToolUsageTracker
from autocontext.agents.llm_client import DeterministicDevClient
from autocontext.agents.orchestrator import AgentOrchestrator
from autocontext.agents.skeptic import SkepticReview
from autocontext.agents.types import AgentOutputs
from autocontext.config.settings import AppSettings
from autocontext.execution.supervisor import ExecutionSupervisor
from autocontext.harness.core.types import ModelResponse, RoleExecution, RoleUsage
from autocontext.harness.evaluation.types import EvaluationResult, EvaluationSummary
from autocontext.harness.pipeline.holdout import HoldoutResult
from autocontext.loop.stage_types import GenerationContext, StageResult
from autocontext.loop.stages import (
    stage_agent_generation,
    stage_curator_gate,
    stage_knowledge_setup,
    stage_persistence,
    stage_tournament,
)
from autocontext.scenarios.base import (
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
        artifacts.read_mutation_replay.return_value = ""
        artifacts.read_latest_weakness_reports_markdown.return_value = ""
        artifacts.read_latest_progress_reports_markdown.return_value = ""
        artifacts.read_latest_advance_analysis.return_value = ""
        artifacts.read_progress.return_value = None
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
        artifacts.read_mutation_replay.return_value = ""
        artifacts.read_latest_weakness_reports_markdown.return_value = ""
        artifacts.read_latest_progress_reports_markdown.return_value = ""
        artifacts.read_latest_advance_analysis.return_value = ""
        artifacts.read_progress.return_value = None
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

    def test_includes_mutation_replay_in_prompt_context(self) -> None:
        artifacts = MagicMock()
        artifacts.read_playbook.return_value = ""
        artifacts.read_tool_context.return_value = ""
        artifacts.read_skills.return_value = ""
        artifacts.read_mutation_replay.return_value = "Context mutations since last checkpoint:\n- gen 2: playbook_updated"
        artifacts.read_latest_weakness_reports_markdown.return_value = ""
        artifacts.read_latest_progress_reports_markdown.return_value = ""
        artifacts.read_latest_advance_analysis.return_value = ""
        artifacts.read_progress.return_value = None
        trajectory = MagicMock()
        trajectory.build_trajectory.return_value = ""
        trajectory.build_strategy_registry.return_value = ""
        trajectory.build_experiment_log.return_value = ""
        ctx = _make_ctx()

        result = stage_knowledge_setup(ctx, artifacts=artifacts, trajectory_builder=trajectory)

        assert result.prompts is not None
        assert "Context mutations since last checkpoint" in result.prompts.competitor

    def test_includes_recent_weakness_reports_in_prompt_context(self) -> None:
        artifacts = MagicMock()
        artifacts.read_playbook.return_value = ""
        artifacts.read_tool_context.return_value = ""
        artifacts.read_skills.return_value = ""
        artifacts.read_mutation_replay.return_value = ""
        artifacts.read_latest_weakness_reports_markdown.return_value = (
            "# Weakness Report: run_1\n"
            "## [HIGH] dead_end_pattern\n"
            "Repeated rollbacks detected"
        )
        artifacts.read_latest_progress_reports_markdown.return_value = ""
        artifacts.read_latest_advance_analysis.return_value = ""
        artifacts.read_progress.return_value = None
        trajectory = MagicMock()
        trajectory.build_trajectory.return_value = ""
        trajectory.build_strategy_registry.return_value = ""
        trajectory.build_experiment_log.return_value = ""
        ctx = _make_ctx()

        result = stage_knowledge_setup(ctx, artifacts=artifacts, trajectory_builder=trajectory)

        assert result.prompts is not None
        assert "Recent weakness reports:" in result.prompts.competitor
        assert "dead_end_pattern" in result.prompts.competitor

    def test_includes_recent_progress_reports_in_prompt_context(self) -> None:
        artifacts = MagicMock()
        artifacts.read_playbook.return_value = ""
        artifacts.read_tool_context.return_value = ""
        artifacts.read_skills.return_value = ""
        artifacts.read_mutation_replay.return_value = ""
        artifacts.read_latest_weakness_reports_markdown.return_value = ""
        artifacts.read_latest_progress_reports_markdown.return_value = (
            "# Progress Report: run_2\n"
            "- Total cost: $0.0240\n"
            "- Tokens per advance: 3,400"
        )
        artifacts.read_latest_advance_analysis.return_value = ""
        artifacts.read_progress.return_value = None
        trajectory = MagicMock()
        trajectory.build_trajectory.return_value = ""
        trajectory.build_strategy_registry.return_value = ""
        trajectory.build_experiment_log.return_value = ""
        ctx = _make_ctx()

        result = stage_knowledge_setup(ctx, artifacts=artifacts, trajectory_builder=trajectory)

        assert result.prompts is not None
        assert "Recent progress reports:" in result.prompts.competitor
        assert "Tokens per advance" in result.prompts.competitor

    def test_includes_role_specific_notebook_context_in_live_prompt_bundle(self) -> None:
        artifacts = MagicMock()
        artifacts.read_playbook.return_value = ""
        artifacts.read_tool_context.return_value = ""
        artifacts.read_skills.return_value = ""
        artifacts.read_mutation_replay.return_value = ""
        artifacts.read_latest_weakness_reports_markdown.return_value = ""
        artifacts.read_latest_progress_reports_markdown.return_value = ""
        artifacts.read_latest_advance_analysis.return_value = ""
        artifacts.read_progress.return_value = None
        artifacts.read_notebook.return_value = {
            "session_id": "run_test",
            "scenario_name": "test_scenario",
            "current_objective": "Push toward stable defense-first play",
            "current_hypotheses": ["Lower aggression should reduce rollback risk"],
            "operator_observations": ["The latest analyst output over-indexes on offense"],
            "follow_ups": ["Try aggression <= 0.4 next generation"],
            "unresolved_questions": ["Does defense trade off too much score ceiling?"],
        }
        trajectory = MagicMock()
        trajectory.build_trajectory.return_value = ""
        trajectory.build_strategy_registry.return_value = ""
        trajectory.build_experiment_log.return_value = ""
        ctx = _make_ctx()

        result = stage_knowledge_setup(ctx, artifacts=artifacts, trajectory_builder=trajectory)

        assert result.prompts is not None
        assert "Push toward stable defense-first play" in result.prompts.competitor
        assert "Try aggression <= 0.4 next generation" in result.prompts.competitor
        assert "The latest analyst output over-indexes on offense" not in result.prompts.competitor
        assert "The latest analyst output over-indexes on offense" in result.prompts.analyst
        assert "Try aggression <= 0.4 next generation" not in result.prompts.analyst

    def test_includes_prior_analyst_feedback_in_analyst_prompt(self) -> None:
        artifacts = MagicMock()
        artifacts.read_playbook.return_value = ""
        artifacts.read_tool_context.return_value = ""
        artifacts.read_skills.return_value = ""
        artifacts.read_mutation_replay.return_value = ""
        artifacts.read_latest_weakness_reports_markdown.return_value = ""
        artifacts.read_latest_progress_reports_markdown.return_value = ""
        artifacts.read_latest_advance_analysis.return_value = ""
        artifacts.read_progress.return_value = None
        artifacts.read_latest_analyst_rating.return_value = AnalystRating(
            actionability=2,
            specificity=3,
            correctness=4,
            rationale="Recommendations were still too vague.",
            generation=1,
        )
        artifacts.read_tool_usage_report.return_value = ""
        trajectory = MagicMock()
        trajectory.build_trajectory.return_value = ""
        trajectory.build_strategy_registry.return_value = ""
        trajectory.build_experiment_log.return_value = ""
        ctx = _make_ctx()
        ctx.generation = 2

        result = stage_knowledge_setup(ctx, artifacts=artifacts, trajectory_builder=trajectory)

        assert result.prompts is not None
        assert "Previous Analysis Quality" in result.prompts.analyst
        assert "too vague" in result.prompts.analyst.lower()
        assert "Previous Analysis Quality" not in result.prompts.competitor

    def test_includes_tool_usage_report_in_architect_prompt(self) -> None:
        artifacts = MagicMock()
        artifacts.read_playbook.return_value = ""
        artifacts.read_tool_context.return_value = ""
        artifacts.read_skills.return_value = ""
        artifacts.read_mutation_replay.return_value = ""
        artifacts.read_latest_weakness_reports_markdown.return_value = ""
        artifacts.read_latest_progress_reports_markdown.return_value = ""
        artifacts.read_latest_advance_analysis.return_value = ""
        artifacts.read_progress.return_value = None
        artifacts.read_latest_analyst_rating.return_value = None
        artifacts.read_tool_usage_report.return_value = (
            "Tool utilization (last 5 gens):\n- path_optimizer: used 1/5 gens (LOW)"
        )
        trajectory = MagicMock()
        trajectory.build_trajectory.return_value = ""
        trajectory.build_strategy_registry.return_value = ""
        trajectory.build_experiment_log.return_value = ""
        ctx = _make_ctx()
        ctx.generation = 3

        result = stage_knowledge_setup(ctx, artifacts=artifacts, trajectory_builder=trajectory)

        assert result.prompts is not None
        assert "Tool utilization" in result.prompts.architect
        assert "path_optimizer" in result.prompts.architect
        assert "Tool utilization" not in result.prompts.analyst

    def test_includes_prior_hint_feedback_in_coach_prompt_only(self) -> None:
        from autocontext.agents.hint_feedback import HintFeedback

        artifacts = MagicMock()
        artifacts.read_playbook.return_value = ""
        artifacts.read_tool_context.return_value = ""
        artifacts.read_skills.return_value = ""
        artifacts.read_mutation_replay.return_value = ""
        artifacts.read_latest_weakness_reports_markdown.return_value = ""
        artifacts.read_latest_progress_reports_markdown.return_value = ""
        artifacts.read_latest_advance_analysis.return_value = ""
        artifacts.read_progress.return_value = None
        artifacts.read_latest_analyst_rating.return_value = None
        artifacts.read_tool_usage_report.return_value = ""
        artifacts.read_latest_hint_feedback.return_value = HintFeedback(
            helpful=["corners worked"],
            misleading=["rush center line"],
            missing=["late-game edge defense"],
            generation=1,
        )
        trajectory = MagicMock()
        trajectory.build_trajectory.return_value = ""
        trajectory.build_strategy_registry.return_value = ""
        trajectory.build_experiment_log.return_value = ""
        ctx = _make_ctx()
        ctx.generation = 2

        result = stage_knowledge_setup(ctx, artifacts=artifacts, trajectory_builder=trajectory)

        assert result.prompts is not None
        assert "Competitor Hint Feedback" in result.prompts.coach
        assert "corners worked" in result.prompts.coach
        assert "Competitor Hint Feedback" not in result.prompts.competitor
        assert "Competitor Hint Feedback" not in result.prompts.analyst

    def test_freshness_decay_omits_stale_hints_from_prompt_context(self) -> None:
        from autocontext.knowledge.hint_volume import HintManager, HintVolumePolicy

        artifacts = MagicMock()
        artifacts.read_playbook.return_value = ""
        artifacts.read_tool_context.return_value = ""
        artifacts.read_skills.return_value = ""
        artifacts.read_mutation_replay.return_value = ""
        artifacts.read_latest_weakness_reports_markdown.return_value = ""
        artifacts.read_latest_progress_reports_markdown.return_value = ""
        artifacts.read_latest_advance_analysis.return_value = ""
        artifacts.read_progress.return_value = None
        artifacts.read_latest_analyst_rating.return_value = None
        artifacts.read_tool_usage_report.return_value = ""
        artifacts.read_latest_hint_feedback.return_value = None
        artifacts.read_notebook.return_value = None

        manager = HintManager(HintVolumePolicy(max_hints=5))
        manager.add("Stale corner hint", generation=1, impact_score=0.9)
        artifacts.read_hint_manager.return_value = manager

        trajectory = MagicMock()
        trajectory.build_trajectory.return_value = ""
        trajectory.build_strategy_registry.return_value = ""
        trajectory.build_experiment_log.return_value = ""

        settings = AppSettings(
            agent_provider="deterministic",
            evidence_freshness_enabled=True,
            evidence_freshness_max_age_gens=5,
            hint_volume_enabled=True,
        )
        ctx = _make_ctx(settings=settings)
        ctx.generation = 20

        result = stage_knowledge_setup(ctx, artifacts=artifacts, trajectory_builder=trajectory)

        assert result.prompts is not None
        assert "Coach hints for competitor:\n- Stale corner hint" not in result.prompts.competitor
        assert "Hint freshness warnings" in result.prompts.competitor

    def test_freshness_decay_filters_stale_notebook_context(self) -> None:
        artifacts = MagicMock()
        artifacts.read_playbook.return_value = ""
        artifacts.read_tool_context.return_value = ""
        artifacts.read_skills.return_value = ""
        artifacts.read_mutation_replay.return_value = ""
        artifacts.read_latest_weakness_reports_markdown.return_value = ""
        artifacts.read_latest_progress_reports_markdown.return_value = ""
        artifacts.read_latest_advance_analysis.return_value = ""
        artifacts.read_progress.return_value = None
        artifacts.read_latest_analyst_rating.return_value = None
        artifacts.read_tool_usage_report.return_value = ""
        artifacts.read_latest_hint_feedback.return_value = None
        artifacts.read_hint_manager.return_value = MagicMock(active_hints=lambda: [])
        artifacts.read_notebook.return_value = {
            "session_id": "run_test",
            "scenario_name": "test_scenario",
            "current_objective": "Old notebook objective",
            "follow_ups": ["Try the risky line again"],
            "best_generation": 1,
            "best_score": 0.9,
        }

        trajectory = MagicMock()
        trajectory.build_trajectory.return_value = ""
        trajectory.build_strategy_registry.return_value = ""
        trajectory.build_experiment_log.return_value = ""

        settings = AppSettings(
            agent_provider="deterministic",
            evidence_freshness_enabled=True,
            evidence_freshness_max_age_gens=5,
        )
        ctx = _make_ctx(settings=settings)
        ctx.generation = 20

        result = stage_knowledge_setup(ctx, artifacts=artifacts, trajectory_builder=trajectory)

        assert result.prompts is not None
        assert "Old notebook objective" not in result.prompts.competitor
        assert "Notebook freshness warnings" in result.prompts.competitor


# ---------- TestStageAgentGeneration ----------


class TestStageAgentGeneration:
    def test_populates_outputs_and_strategy(self) -> None:
        settings = _make_settings()
        client = DeterministicDevClient()
        orch = AgentOrchestrator(client=client, settings=settings)
        scenario = _make_scenario_mock()
        ctx = _make_ctx(settings=settings, scenario=scenario)

        # Simulate stage 1 ran
        from autocontext.prompts.templates import build_prompt_bundle

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

        from autocontext.prompts.templates import build_prompt_bundle

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

        from autocontext.prompts.templates import build_prompt_bundle

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

        sqlite.append_generation_agent_activity.assert_called_once()
        _, kwargs = sqlite.append_generation_agent_activity.call_args
        assert len(kwargs["outputs"]) == 4
        assert len(kwargs["role_metrics"]) == 5

    def test_updates_tool_usage_feedback_from_competitor_raw_text(self) -> None:
        scenario = _make_scenario_mock()
        ctx = _make_ctx(settings=_make_settings(), scenario=scenario)

        from autocontext.agents.contracts import CompetitorOutput
        from autocontext.prompts.templates import build_prompt_bundle

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

        role_exec = RoleExecution(
            role="competitor",
            content="Use cluster_evaluator to choose safer paths.",
            usage=RoleUsage(input_tokens=10, output_tokens=5, latency_ms=1, model="test"),
            subagent_id="competitor",
            status="completed",
        )
        outputs = AgentOutputs(
            strategy={"aggression": 0.5},
            analysis_markdown="analysis",
            coach_markdown="coach",
            coach_playbook="playbook",
            coach_lessons="",
            coach_competitor_hints="",
            architect_markdown="architect",
            architect_tools=[],
            role_executions=[role_exec],
            competitor_output=CompetitorOutput(
                raw_text="Use cluster_evaluator to choose safer paths.",
                strategy={"aggression": 0.5},
                reasoning="Use cluster_evaluator to choose safer paths.",
            ),
        )
        orchestrator = MagicMock()
        orchestrator.run_generation.return_value = outputs
        artifacts = MagicMock()
        artifacts.persist_tools.return_value = []
        artifacts.list_tool_names.return_value = ["cluster_evaluator", "path_optimizer"]
        artifacts.read_tool_usage_tracker.return_value = ToolUsageTracker(
            known_tools=["cluster_evaluator", "path_optimizer"],
        )
        sqlite = MagicMock()

        stage_agent_generation(ctx, orchestrator=orchestrator, artifacts=artifacts, sqlite=sqlite)

        written_tracker = artifacts.write_tool_usage_tracker.call_args.args[1]
        assert isinstance(written_tracker, ToolUsageTracker)
        assert written_tracker.get_stats()["cluster_evaluator"].total_refs == 1


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
        gate = MagicMock()
        gate.evaluate.return_value = MagicMock(decision=gate_decision, reason=gate_reason)
        events = MagicMock()
        sqlite = MagicMock()
        artifacts = MagicMock()
        return stage_tournament(
            ctx,
            supervisor=supervisor,
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

    def test_uses_resolve_gate_decision_and_apply_tournament_outcome_helpers(self) -> None:
        """The live stage should delegate gate resolution and outcome application."""
        ctx = _make_tournament_ctx(previous_best=0.25)
        supervisor = _make_inline_supervisor()
        gate = MagicMock()
        gate.evaluate.return_value = MagicMock(decision="rollback", reason="inline gate should not run")
        events = MagicMock()
        sqlite = MagicMock()
        artifacts = MagicMock()

        with (
            patch("autocontext.loop.stages.resolve_gate_decision") as resolve_gate,
            patch("autocontext.loop.stages.apply_tournament_outcome") as apply_outcome,
        ):
            resolve_gate.return_value = MagicMock(
                decision="advance",
                delta=0.42,
                reason="helper reason",
                is_rapid=False,
            )
            apply_outcome.return_value = {
                "gate_delta": 0.42,
                "previous_best": 0.91,
                "challenger_elo": 1234.0,
                "score_history": [0.91],
                "gate_decision_history": ["advance"],
            }

            result = stage_tournament(
                ctx,
                supervisor=supervisor,
                gate=gate,
                events=events,
                sqlite=sqlite,
                artifacts=artifacts,
                agents=None,
            )

        resolve_gate.assert_called_once()
        apply_outcome.assert_called_once()
        gate.evaluate.assert_not_called()
        assert result.gate_decision == "advance"
        assert result.gate_delta == 0.42
        assert result.previous_best == 0.91
        assert result.challenger_elo == 1234.0
        assert result.score_history == [0.91]
        assert result.gate_decision_history == ["advance"]

    def test_emits_dimension_metadata_for_dimensional_game_scenarios(self) -> None:
        class _DimensionalScenario(_FakeScenario):
            def scoring_dimensions(self) -> list[dict[str, Any]] | None:
                return [
                    {"name": "control", "weight": 0.6},
                    {"name": "tempo", "weight": 0.4},
                ]

            def get_result(self, state: Mapping[str, Any]) -> Result:
                score = float(state.get("score", 0.5))
                return Result(
                    score=score,
                    summary="test",
                    replay=[],
                    metrics={
                        "control": round(min(1.0, score + 0.1), 4),
                        "tempo": round(max(0.0, score - 0.1), 4),
                    },
                )

        ctx = _make_tournament_ctx(scenario=_DimensionalScenario())
        supervisor = _make_inline_supervisor()
        gate = MagicMock()
        gate.evaluate.return_value = MagicMock(decision="advance", reason="improved")
        events = MagicMock()
        sqlite = MagicMock()
        sqlite.get_generation_trajectory.return_value = [
            {
                "generation_index": 0,
                "dimension_summary": {
                    "best_dimensions": {"control": 0.95, "tempo": 0.2},
                },
            },
        ]
        artifacts = MagicMock()

        result = stage_tournament(
            ctx,
            supervisor=supervisor,
            gate=gate,
            events=events,
            sqlite=sqlite,
            artifacts=artifacts,
            agents=None,
        )

        assert result.tournament is not None
        assert result.tournament.best_dimensions
        tournament_events = [
            call for call in events.emit.call_args_list if call[0][0] == "tournament_completed"
        ]
        assert tournament_events
        payload = tournament_events[-1][0][1]
        assert payload["best_dimensions"]
        assert "control" in payload["best_dimensions"]
        assert payload["dimension_regressions"]

    def test_self_play_pool_is_used_in_live_tournament_path(self) -> None:
        settings = AppSettings(
            agent_provider="deterministic",
            matches_per_generation=2,
            self_play_enabled=True,
            self_play_pool_size=3,
            self_play_weight=1.0,
        )
        ctx = _make_tournament_ctx(
            settings=settings,
            strategy={"aggression": 0.2},
        )
        ctx.generation = 2
        supervisor = _make_inline_supervisor()
        gate = MagicMock()
        gate.evaluate.return_value = MagicMock(decision="advance", reason="improved")
        events = MagicMock()
        sqlite = MagicMock()
        sqlite.get_self_play_strategy_history.return_value = [
            {
                "generation_index": 1,
                "content": json.dumps({"aggression": 0.9}),
                "best_score": 0.9,
                "gate_decision": "advance",
                "elo": 1110.0,
            },
        ]
        sqlite.get_generation_trajectory.return_value = []
        artifacts = MagicMock()

        result = stage_tournament(
            ctx,
            supervisor=supervisor,
            gate=gate,
            events=events,
            sqlite=sqlite,
            artifacts=artifacts,
            agents=None,
        )

        assert result.tournament is not None
        assert result.tournament.self_play_summary["self_play_matches"] == 2
        assert result.tournament.mean_score < 0.5
        tournament_events = [
            call for call in events.emit.call_args_list if call[0][0] == "tournament_completed"
        ]
        assert tournament_events
        payload = tournament_events[-1][0][1]
        assert payload["self_play"]["self_play_matches"] == 2

    def test_holdout_failure_blocks_advance_in_live_stage(self) -> None:
        """A generation can win locally and still be blocked by holdout regression."""
        settings = AppSettings(
            agent_provider="deterministic",
            max_retries=0,
            holdout_enabled=True,
            holdout_min_score=0.6,
            holdout_max_regression_gap=0.05,
        )
        ctx = _make_tournament_ctx(previous_best=0.0, settings=settings)
        supervisor = _make_inline_supervisor()
        gate = MagicMock()
        gate.evaluate.return_value = MagicMock(decision="rollback", reason="inline gate should not run")
        events = MagicMock()
        sqlite = MagicMock()
        artifacts = MagicMock()

        with patch("autocontext.loop.stages.resolve_gate_decision") as resolve_gate:
            resolve_gate.return_value = MagicMock(
                decision="advance",
                delta=0.42,
                reason="helper reason",
                is_rapid=False,
            )
            result = stage_tournament(
                ctx,
                supervisor=supervisor,
                gate=gate,
                events=events,
                sqlite=sqlite,
                artifacts=artifacts,
                agents=None,
            )

        assert result.gate_decision == "rollback"
        assert result.holdout_result is not None
        assert result.holdout_result.passed is False
        gate_events = [call for call in events.emit.call_args_list if call.args[0] == "gate_decided"]
        assert gate_events
        assert gate_events[-1].args[1]["holdout"]["passed"] is False

    def test_family_override_can_disable_holdout(self) -> None:
        """Family-level holdout overrides should apply to the live gate path."""
        settings = AppSettings(
            agent_provider="deterministic",
            holdout_enabled=True,
            holdout_family_policies={"parametric": {"enabled": False}},
        )
        ctx = _make_tournament_ctx(previous_best=0.0, settings=settings)
        supervisor = _make_inline_supervisor()
        gate = MagicMock()
        gate.evaluate.return_value = MagicMock(decision="rollback", reason="inline gate should not run")
        events = MagicMock()
        sqlite = MagicMock()
        artifacts = MagicMock()

        with patch("autocontext.loop.stages.resolve_gate_decision") as resolve_gate:
            resolve_gate.return_value = MagicMock(
                decision="advance",
                delta=0.42,
                reason="helper reason",
                is_rapid=False,
            )
            result = stage_tournament(
                ctx,
                supervisor=supervisor,
                gate=gate,
                events=events,
                sqlite=sqlite,
                artifacts=artifacts,
                agents=None,
            )

        assert result.gate_decision == "advance"
        assert result.holdout_result is None


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

    def test_curator_receives_skeptic_review_section(self) -> None:
        curator = MagicMock()
        curator.assess_playbook_quality.return_value = (
            MagicMock(decision="accept", playbook="", score=7, reasoning="ok"),
            RoleExecution(
                role="curator",
                content="ok",
                usage=RoleUsage(input_tokens=10, output_tokens=5, latency_ms=1, model="test"),
                subagent_id="curator-test",
                status="completed",
            ),
        )
        ctx = _make_curator_ctx(gate_decision="advance", coach_playbook="new playbook")
        ctx.skeptic_review = SkepticReview(
            risk_level="high",
            concerns=["Overfit to a narrow opponent slice"],
            recommendation="caution",
            confidence=8,
            reasoning="Be careful.",
        )
        artifacts = MagicMock()
        artifacts.read_playbook.return_value = "current playbook"
        trajectory_builder = MagicMock()
        trajectory_builder.build_trajectory.return_value = "Gen1: 0.5"

        stage_curator_gate(
            ctx,
            curator=curator,
            artifacts=artifacts,
            trajectory_builder=trajectory_builder,
            sqlite=MagicMock(),
            events=MagicMock(),
        )

        kwargs = curator.assess_playbook_quality.call_args.kwargs
        assert "skeptic_review_section" in kwargs
        assert "Risk level: high" in kwargs["skeptic_review_section"]
        assert "Recommendation: caution" in kwargs["skeptic_review_section"]
        assert "Overfit to a narrow opponent slice" in kwargs["skeptic_review_section"]

    def test_persists_analyst_rating_feedback(self) -> None:
        curator = MagicMock()
        curator.rate_analyst_output.return_value = (
            AnalystRating(
                actionability=4,
                specificity=3,
                correctness=5,
                rationale="Strong evidence, but one recommendation could be more specific.",
                generation=1,
            ),
            RoleExecution(
                role="curator",
                content='{"actionability": 4}',
                usage=RoleUsage(input_tokens=12, output_tokens=6, latency_ms=1, model="test"),
                subagent_id="curator-rating",
                status="completed",
            ),
        )
        curator.assess_playbook_quality.return_value = (
            MagicMock(decision="accept", playbook="", score=7, reasoning="ok"),
            RoleExecution(
                role="curator",
                content="ok",
                usage=RoleUsage(input_tokens=10, output_tokens=5, latency_ms=1, model="test"),
                subagent_id="curator-test",
                status="completed",
            ),
        )
        ctx = _make_curator_ctx(gate_decision="advance", coach_playbook="new playbook")
        ctx.outputs.analysis_markdown = (
            "## Findings\n- Concrete issue\n\n"
            "## Root Causes\n- Cause\n\n"
            "## Actionable Recommendations\n- Fix it"
        )
        artifacts = MagicMock()
        artifacts.read_playbook.return_value = "current playbook"
        trajectory_builder = MagicMock()
        trajectory_builder.build_trajectory.return_value = "Gen1: 0.5"
        sqlite = MagicMock()
        events = MagicMock()

        stage_curator_gate(
            ctx,
            curator=curator,
            artifacts=artifacts,
            trajectory_builder=trajectory_builder,
            sqlite=sqlite,
            events=events,
        )

        curator.rate_analyst_output.assert_called_once()
        artifacts.write_analyst_rating.assert_called_once()
        outputs = sqlite.append_generation_agent_activity.call_args_list[0].kwargs["outputs"]
        assert any(role == "curator_analyst_rating" for role, _ in outputs)


# ---------- Helpers for persistence stage tests ----------


def _make_persistence_ctx(
    gate_decision: str = "advance",
    coach_playbook: str = "Updated playbook",
    coach_lessons: str = "- Lesson one\n- Lesson two",
    coach_competitor_hints: str = "try aggression=0.9",
    replay_narrative: str = "Player captured the flag at step 5",
    current_strategy: dict | None = None,
    generation: int = 3,
    ablation: bool = False,
    curator_enabled: bool = False,
) -> GenerationContext:
    """Build a GenerationContext pre-populated for persistence stage tests."""
    settings = AppSettings(
        agent_provider="deterministic",
        ablation_no_feedback=ablation,
        curator_enabled=curator_enabled,
    )
    outputs = MagicMock(spec=AgentOutputs)
    outputs.analysis_markdown = "## Analysis output"
    outputs.coach_markdown = "## Coach output"
    outputs.coach_playbook = coach_playbook
    outputs.coach_lessons = coach_lessons
    outputs.coach_competitor_hints = coach_competitor_hints
    outputs.architect_markdown = "## Architect output"

    # Build mock execution outputs for backward-compatible access via metadata
    exec_output_1 = MagicMock()
    exec_output_1.result.score = 0.75
    exec_output_1.result.passed_validation = True
    exec_output_1.result.validation_errors = []
    exec_output_1.replay.model_dump.return_value = {"scenario": "test", "seed": 1001, "timeline": []}

    exec_output_2 = MagicMock()
    exec_output_2.result.score = 0.82
    exec_output_2.result.passed_validation = True
    exec_output_2.result.validation_errors = []
    exec_output_2.replay.model_dump.return_value = {"scenario": "test", "seed": 1002, "timeline": []}

    # Build EvaluationResult objects wrapping the execution outputs
    eval_result_1 = EvaluationResult(
        score=0.75,
        passed=True,
        errors=[],
        metadata={"execution_output": exec_output_1},
    )
    eval_result_2 = EvaluationResult(
        score=0.82,
        passed=True,
        errors=[],
        metadata={"execution_output": exec_output_2},
    )

    tournament = EvaluationSummary(
        mean_score=0.785,
        best_score=0.82,
        wins=2,
        losses=0,
        elo_after=1020.0,
        results=[eval_result_1, eval_result_2],
    )

    strategy = current_strategy or {"aggression": 0.8}

    return GenerationContext(
        run_id="run_persist",
        scenario_name="test_scenario",
        scenario=MagicMock(),
        generation=generation,
        settings=settings,
        previous_best=0.7,
        challenger_elo=1010.0,
        score_history=[0.5, 0.7],
        gate_decision_history=["advance", "advance"],
        coach_competitor_hints="old hints",
        replay_narrative=replay_narrative,
        gate_decision=gate_decision,
        gate_delta=0.12,
        current_strategy=strategy,
        outputs=outputs,
        tournament=tournament,
    )


# ---------- TestStagePersistence ----------


class TestStagePersistence:
    def test_upserts_generation_and_persists_artifacts(self) -> None:
        """Verify sqlite.upsert_generation, artifacts.persist_generation, and persist_skill_note are called."""
        ctx = _make_persistence_ctx()
        artifacts = MagicMock()
        artifacts.read_skill_lessons_raw.return_value = []
        sqlite = MagicMock()
        events = MagicMock()
        trajectory = MagicMock()

        stage_persistence(
            ctx,
            artifacts=artifacts,
            sqlite=sqlite,
            trajectory_builder=trajectory,
            events=events,
            curator=None,
        )

        sqlite.upsert_generation.assert_called_once()
        call_kwargs = sqlite.upsert_generation.call_args
        assert call_kwargs[1]["status"] == "completed"
        assert call_kwargs[1]["gate_decision"] == "advance"

        artifacts.persist_generation.assert_called_once()
        artifacts.persist_skill_note.assert_called_once()

    def test_advance_passes_coach_playbook(self) -> None:
        """On advance, persist_generation receives the non-empty coach_playbook."""
        ctx = _make_persistence_ctx(gate_decision="advance", coach_playbook="My awesome playbook")
        artifacts = MagicMock()
        artifacts.read_skill_lessons_raw.return_value = []
        sqlite = MagicMock()
        events = MagicMock()
        trajectory = MagicMock()

        stage_persistence(
            ctx,
            artifacts=artifacts,
            sqlite=sqlite,
            trajectory_builder=trajectory,
            events=events,
            curator=None,
        )

        persist_call = artifacts.persist_generation.call_args
        assert persist_call[1]["coach_playbook"] == "My awesome playbook"

    def test_persists_pi_traces_by_role(self) -> None:
        ctx = _make_persistence_ctx()
        ctx.outputs.role_executions = [
            RoleExecution(
                role="competitor",
                content="result",
                usage=RoleUsage(input_tokens=1, output_tokens=1, latency_ms=1, model="pi"),
                subagent_id="competitor-1",
                status="completed",
                metadata={"pi_trace": MagicMock()},
            )
        ]
        artifacts = MagicMock()
        artifacts.read_skill_lessons_raw.return_value = []
        sqlite = MagicMock()
        events = MagicMock()
        trajectory = MagicMock()

        stage_persistence(
            ctx,
            artifacts=artifacts,
            sqlite=sqlite,
            trajectory_builder=trajectory,
            events=events,
            curator=None,
        )

        artifacts.persist_pi_session.assert_called_once_with(
            ctx.run_id,
            ctx.generation,
            ctx.outputs.role_executions[0].metadata["pi_trace"],
            role="competitor",
        )

    def test_rollback_generates_rollback_lesson(self) -> None:
        """On rollback, persist_skill_note receives a lesson containing 'ROLLBACK'."""
        ctx = _make_persistence_ctx(gate_decision="rollback")
        artifacts = MagicMock()
        artifacts.read_skill_lessons_raw.return_value = []
        sqlite = MagicMock()
        events = MagicMock()
        trajectory = MagicMock()

        stage_persistence(
            ctx,
            artifacts=artifacts,
            sqlite=sqlite,
            trajectory_builder=trajectory,
            events=events,
            curator=None,
        )

        skill_call = artifacts.persist_skill_note.call_args
        assert skill_call[1]["decision"] == "rollback"
        assert "ROLLBACK" in skill_call[1]["lessons"]

    def test_advance_writes_hints(self) -> None:
        """On advance, the live path persists structured hint state."""
        ctx = _make_persistence_ctx(gate_decision="advance", coach_competitor_hints="new hints")
        artifacts = MagicMock()
        artifacts.read_skill_lessons_raw.return_value = []
        artifacts.read_hint_manager.return_value = MagicMock(
            active_hints=lambda: [],
            archived_hints=lambda: [],
            merge_hint_text=MagicMock(),
            format_for_competitor=MagicMock(return_value="- new hints"),
        )
        sqlite = MagicMock()
        events = MagicMock()
        trajectory = MagicMock()

        stage_persistence(
            ctx,
            artifacts=artifacts,
            sqlite=sqlite,
            trajectory_builder=trajectory,
            events=events,
            curator=None,
        )

        artifacts.write_hint_manager.assert_called_once()
        assert ctx.coach_competitor_hints == "- new hints"

    def test_inserts_matches(self) -> None:
        """sqlite.insert_match is called once per tournament output."""
        ctx = _make_persistence_ctx()
        artifacts = MagicMock()
        artifacts.read_skill_lessons_raw.return_value = []
        sqlite = MagicMock()
        events = MagicMock()
        trajectory = MagicMock()

        stage_persistence(
            ctx,
            artifacts=artifacts,
            sqlite=sqlite,
            trajectory_builder=trajectory,
            events=events,
            curator=None,
        )

        # Tournament has 2 outputs, so insert_match should be called twice
        assert sqlite.insert_match.call_count == 2

    def test_emits_generation_completed(self) -> None:
        """events.emit is called with 'generation_completed'."""
        ctx = _make_persistence_ctx()
        artifacts = MagicMock()
        artifacts.read_skill_lessons_raw.return_value = []
        sqlite = MagicMock()
        events = MagicMock()
        trajectory = MagicMock()

        stage_persistence(
            ctx,
            artifacts=artifacts,
            sqlite=sqlite,
            trajectory_builder=trajectory,
            events=events,
            curator=None,
        )

        # Find the generation_completed emit call
        emit_calls = events.emit.call_args_list
        gen_completed_calls = [c for c in emit_calls if c[0][0] == "generation_completed"]
        assert len(gen_completed_calls) == 1

    def test_persistence_surfaces_holdout_metrics(self) -> None:
        """Holdout metrics should flow into persisted metrics and completion events."""
        ctx = _make_persistence_ctx()
        ctx.holdout_result = HoldoutResult(
            holdout_mean_score=0.54,
            holdout_scores=[0.5, 0.56, 0.56],
            in_sample_score=0.7,
            generalization_gap=0.16,
            passed=False,
            reason="Holdout blocked advance",
        )
        artifacts = MagicMock()
        artifacts.read_skill_lessons_raw.return_value = []
        sqlite = MagicMock()
        events = MagicMock()
        trajectory = MagicMock()

        stage_persistence(
            ctx,
            artifacts=artifacts,
            sqlite=sqlite,
            trajectory_builder=trajectory,
            events=events,
            curator=None,
        )

        persist_kwargs = artifacts.persist_generation.call_args.kwargs
        assert persist_kwargs["metrics"]["holdout"]["passed"] is False
        gen_completed_calls = [c for c in events.emit.call_args_list if c[0][0] == "generation_completed"]
        assert gen_completed_calls
        assert gen_completed_calls[-1][0][1]["holdout"]["passed"] is False

    def test_carries_forward_coach_hints(self) -> None:
        """ctx.coach_competitor_hints is updated from outputs."""
        ctx = _make_persistence_ctx(coach_competitor_hints="updated hints from coach")
        artifacts = MagicMock()
        artifacts.read_skill_lessons_raw.return_value = []
        sqlite = MagicMock()
        events = MagicMock()
        trajectory = MagicMock()

        result = stage_persistence(
            ctx,
            artifacts=artifacts,
            sqlite=sqlite,
            trajectory_builder=trajectory,
            events=events,
            curator=None,
        )

        assert result.coach_competitor_hints == "- updated hints from coach"

    def test_collects_and_persists_competitor_hint_feedback(self) -> None:
        from autocontext.knowledge.hint_volume import HintManager, HintVolumePolicy

        ctx = _make_persistence_ctx(
            gate_decision="advance",
            coach_competitor_hints="new hints for next gen",
        )
        ctx.applied_competitor_hints = "old hints used in tournament"
        artifacts = MagicMock()
        artifacts.read_skill_lessons_raw.return_value = []
        artifacts.read_hint_manager.return_value = HintManager(HintVolumePolicy(max_hints=3))
        sqlite = MagicMock()
        events = MagicMock()
        trajectory = MagicMock()
        client = MagicMock()
        client.generate.return_value = ModelResponse(
            text=json.dumps({
                "helpful": "use the corners",
                "misleading": ["over-commit early"],
                "missing": ["late-game cleanup"],
            }),
            usage=RoleUsage(input_tokens=12, output_tokens=9, latency_ms=7, model="test-model"),
        )
        agents = MagicMock()
        agents.resolve_role_execution.return_value = (client, "test-model")
        agents.competitor.model = "fallback-model"

        stage_persistence(
            ctx,
            artifacts=artifacts,
            sqlite=sqlite,
            trajectory_builder=trajectory,
            events=events,
            curator=None,
            agents=agents,
        )

        generate_kwargs = client.generate.call_args.kwargs
        assert "old hints used in tournament" in generate_kwargs["prompt"]
        assert "new hints for next gen" not in generate_kwargs["prompt"]
        artifacts.write_hint_feedback.assert_called_once()
        feedback = artifacts.write_hint_feedback.call_args.args[2]
        assert feedback.helpful == ["use the corners"]
        artifacts.write_hint_manager.assert_called_once()
        persisted_manager = artifacts.write_hint_manager.call_args.args[1]
        assert "new hints for next gen" in persisted_manager.format_for_competitor()
        sqlite.append_generation_agent_activity.assert_called()
        hint_events = [c for c in events.emit.call_args_list if c.args[0] == "hint_feedback_collected"]
        assert hint_events

    def test_hint_volume_control_caps_and_rotates_live_hints(self) -> None:
        from autocontext.knowledge.hint_volume import HintManager, HintVolumePolicy

        settings = AppSettings(
            agent_provider="deterministic",
            hint_volume_enabled=True,
            hint_volume_max_hints=2,
        )
        ctx = _make_persistence_ctx(
            gate_decision="advance",
            coach_competitor_hints="fresh high-value hint",
        )
        ctx.settings = settings
        ctx.applied_competitor_hints = "old strong hint\nold weak hint"
        manager = HintManager(HintVolumePolicy(max_hints=2))
        manager.add("old strong hint", generation=1, impact_score=0.9)
        manager.add("old weak hint", generation=1, impact_score=0.1)

        artifacts = MagicMock()
        artifacts.read_skill_lessons_raw.return_value = []
        artifacts.read_hint_manager.return_value = manager
        sqlite = MagicMock()
        events = MagicMock()
        trajectory = MagicMock()

        with patch(
            "autocontext.loop.stages._collect_hint_feedback",
            return_value=None,
        ):
            result = stage_persistence(
                ctx,
                artifacts=artifacts,
                sqlite=sqlite,
                trajectory_builder=trajectory,
                events=events,
                curator=None,
            )

        artifacts.write_hint_manager.assert_called_once()
        persisted_manager = artifacts.write_hint_manager.call_args.args[1]
        active_texts = [hint.text for hint in persisted_manager.active_hints()]
        archived_texts = [hint.text for hint in persisted_manager.archived_hints()]
        assert active_texts == ["old strong hint", "fresh high-value hint"]
        assert "old weak hint" in archived_texts
        assert "old weak hint" not in result.coach_competitor_hints


# ---------- TestStageTournamentAttempt ----------


class TestStageTournamentAttempt:
    def test_stage_tournament_stores_attempt_on_context(self) -> None:
        """Verify ctx.attempt is populated as an int >= 0 after stage_tournament."""
        ctx = _make_tournament_ctx()
        supervisor = _make_inline_supervisor()
        gate = MagicMock()
        gate.evaluate.return_value = MagicMock(decision="advance", reason="improved")
        events = MagicMock()
        sqlite = MagicMock()
        artifacts = MagicMock()

        result = stage_tournament(
            ctx,
            supervisor=supervisor,
            gate=gate,
            events=events,
            sqlite=sqlite,
            artifacts=artifacts,
            agents=None,
        )
        assert isinstance(result.attempt, int)
        assert result.attempt >= 0

    def test_stage_tournament_persists_revised_competitor_output(self) -> None:
        """Retry-learned competitor strategies should be durable for export."""
        ctx = _make_tournament_ctx(strategy={"aggression": 0.2})
        ctx.prompts = MagicMock(competitor="Improve the strategy.")
        ctx.strategy_interface = '{"aggression": float}'
        supervisor = _make_inline_supervisor()
        gate = MagicMock()
        gate.evaluate.side_effect = [
            MagicMock(decision="retry", reason="not enough improvement"),
            MagicMock(decision="advance", reason="improved"),
        ]
        events = MagicMock()
        sqlite = MagicMock()
        artifacts = MagicMock()
        agents = MagicMock()
        agents.competitor.run.return_value = ('{"aggression": 0.9}', None)
        agents.translator.translate.return_value = ({"aggression": 0.9}, None)

        result = stage_tournament(
            ctx,
            supervisor=supervisor,
            gate=gate,
            events=events,
            sqlite=sqlite,
            artifacts=artifacts,
            agents=agents,
        )

        sqlite.append_agent_output.assert_called_once_with(
            "run_tourn",
            1,
            "competitor",
            '{"aggression": 0.9}',
        )
        assert result.current_strategy == {"aggression": 0.9}

    def test_stage_tournament_uses_retry_prompt_helper(self) -> None:
        """Retry learning should source its prompt from the extracted helper."""
        ctx = _make_tournament_ctx(strategy={"aggression": 0.2})
        ctx.prompts = MagicMock(competitor="Improve the strategy.")
        ctx.strategy_interface = '{"aggression": float}'
        supervisor = _make_inline_supervisor()
        gate = MagicMock()
        gate.evaluate.side_effect = [
            MagicMock(decision="retry", reason="not enough improvement"),
            MagicMock(decision="advance", reason="improved"),
        ]
        events = MagicMock()
        sqlite = MagicMock()
        artifacts = MagicMock()
        agents = MagicMock()
        agents.competitor.run.return_value = ('{"aggression": 0.9}', None)
        agents.translator.translate.return_value = ({"aggression": 0.9}, None)

        with patch("autocontext.loop.stages.build_retry_prompt", return_value="HELPER RETRY PROMPT") as build_prompt:
            stage_tournament(
                ctx,
                supervisor=supervisor,
                gate=gate,
                events=events,
                sqlite=sqlite,
                artifacts=artifacts,
                agents=agents,
            )

        build_prompt.assert_called_once()
        agents.competitor.run.assert_any_call("HELPER RETRY PROMPT", tool_context=ctx.tool_context)


# ---------- TestStageAgentGenerationEvents ----------


class TestStageAgentGenerationEvents:
    def _make_stage2_ctx(self) -> tuple[GenerationContext, AgentOrchestrator]:
        settings = _make_settings()
        client = DeterministicDevClient()
        orch = AgentOrchestrator(client=client, settings=settings)
        scenario = _make_scenario_mock()
        ctx = _make_ctx(settings=settings, scenario=scenario)

        from autocontext.prompts.templates import build_prompt_bundle

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
        return ctx, orch

    def test_stage_agent_generation_emits_agents_started(self) -> None:
        """Verify agents_started event is emitted when events is provided."""
        ctx, orch = self._make_stage2_ctx()
        artifacts = MagicMock()
        artifacts.persist_tools.return_value = []
        sqlite = MagicMock()
        events = MagicMock()

        stage_agent_generation(ctx, orchestrator=orch, artifacts=artifacts, sqlite=sqlite, events=events)

        emit_calls = events.emit.call_args_list
        agents_started_calls = [c for c in emit_calls if c[0][0] == "agents_started"]
        assert len(agents_started_calls) == 1
        payload = agents_started_calls[0][0][1]
        assert payload["run_id"] == ctx.run_id
        assert payload["generation"] == ctx.generation
        assert "competitor" in payload["roles"]
        assert "analyst" in payload["roles"]
        assert "coach" in payload["roles"]
        assert "architect" in payload["roles"]

    def test_stage_agent_generation_emits_role_completed(self) -> None:
        """Verify role_completed events are emitted for each role execution."""
        ctx, orch = self._make_stage2_ctx()
        artifacts = MagicMock()
        artifacts.persist_tools.return_value = []
        sqlite = MagicMock()
        events = MagicMock()

        stage_agent_generation(ctx, orchestrator=orch, artifacts=artifacts, sqlite=sqlite, events=events)

        emit_calls = events.emit.call_args_list
        role_completed_calls = [c for c in emit_calls if c[0][0] == "role_completed"]
        # DeterministicDevClient produces 5 role_executions
        assert len(role_completed_calls) == 5
        for call in role_completed_calls:
            payload = call[0][1]
            assert "run_id" in payload
            assert "generation" in payload
            assert "role" in payload
            assert "latency_ms" in payload
            assert "tokens" in payload


# ---------- TestStagePersistenceCreatedTools ----------


class TestStagePersistenceCreatedTools:
    def test_stage_persistence_emits_created_tools(self) -> None:
        """Verify generation_completed event includes created_tools."""
        ctx = _make_persistence_ctx(gate_decision="advance")
        ctx.created_tools = ["recon_tool.py", "scout_tool.py"]
        artifacts = MagicMock()
        artifacts.read_skill_lessons_raw.return_value = []
        sqlite = MagicMock()
        events = MagicMock()
        trajectory = MagicMock()

        stage_persistence(
            ctx,
            artifacts=artifacts,
            sqlite=sqlite,
            trajectory_builder=trajectory,
            events=events,
            curator=None,
        )

        emit_calls = events.emit.call_args_list
        gen_completed_calls = [c for c in emit_calls if c[0][0] == "generation_completed"]
        assert len(gen_completed_calls) == 1
        payload = gen_completed_calls[0][0][1]
        assert payload["created_tools"] == ["recon_tool.py", "scout_tool.py"]


# ---------- TestStagePersistenceRollbackRetryNote ----------


class TestStagePersistenceRollbackRetryNote:
    def test_stage_persistence_rollback_includes_retry_note(self) -> None:
        """Verify rollback lesson includes 'after N retries' when attempt > 0."""
        ctx = _make_persistence_ctx(gate_decision="rollback")
        ctx.attempt = 3
        artifacts = MagicMock()
        artifacts.read_skill_lessons_raw.return_value = []
        sqlite = MagicMock()
        events = MagicMock()
        trajectory = MagicMock()

        stage_persistence(
            ctx,
            artifacts=artifacts,
            sqlite=sqlite,
            trajectory_builder=trajectory,
            events=events,
            curator=None,
        )

        skill_call = artifacts.persist_skill_note.call_args
        lessons = skill_call[1]["lessons"]
        assert "ROLLBACK after 3 retries" in lessons

    def test_stage_persistence_rollback_no_retry_note_when_zero(self) -> None:
        """Verify rollback lesson does NOT include retry note when attempt == 0."""
        ctx = _make_persistence_ctx(gate_decision="rollback")
        ctx.attempt = 0
        artifacts = MagicMock()
        artifacts.read_skill_lessons_raw.return_value = []
        sqlite = MagicMock()
        events = MagicMock()
        trajectory = MagicMock()

        stage_persistence(
            ctx,
            artifacts=artifacts,
            sqlite=sqlite,
            trajectory_builder=trajectory,
            events=events,
            curator=None,
        )

        skill_call = artifacts.persist_skill_note.call_args
        lessons = skill_call[1]["lessons"]
        assert "ROLLBACK " in lessons
        assert "after" not in lessons.split("ROLLBACK")[1].split("(")[0]
