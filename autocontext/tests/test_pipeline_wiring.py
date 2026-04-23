"""Tests for pipeline wiring: protocol, rapid gate, and tuning in stages."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from autocontext.config.settings import AppSettings
from autocontext.knowledge.protocol import ResearchProtocol
from autocontext.knowledge.rapid_gate import RapidGateResult
from autocontext.knowledge.tuning import TuningConfig
from autocontext.loop.stage_types import GenerationContext

# ---------------------------------------------------------------------------
# Helpers: build minimal GenerationContext with mocked dependencies
# ---------------------------------------------------------------------------


def _make_settings(**overrides: Any) -> AppSettings:
    """Build AppSettings with deterministic defaults plus overrides."""
    defaults: dict[str, Any] = dict(
        agent_provider="deterministic",
        ablation_no_feedback=False,
        progress_json_enabled=False,
        config_adaptive_enabled=False,
        holdout_enabled=False,
        protocol_enabled=False,
        exploration_mode="linear",
        rapid_gens=0,
    )
    defaults.update(overrides)
    return AppSettings(**defaults)


def _make_scenario() -> MagicMock:
    """Build a mock scenario with required methods."""
    scenario = MagicMock()
    scenario.initial_state.return_value = {"board": []}
    scenario.get_observation.return_value = "obs"
    scenario.describe_rules.return_value = "rules"
    scenario.describe_strategy_interface.return_value = '{"move": "string"}'
    scenario.describe_evaluation_criteria.return_value = "criteria"
    scenario.validate_actions.return_value = (True, "")
    scenario.custom_backpressure.return_value = {}
    scenario.replay_to_narrative.return_value = "narrative"
    return scenario


def _make_ctx(settings: AppSettings | None = None, **overrides: Any) -> GenerationContext:
    """Build a GenerationContext with sensible defaults."""
    if settings is None:
        settings = _make_settings()
    defaults: dict[str, Any] = dict(
        run_id="run_1",
        scenario_name="grid_ctf",
        scenario=_make_scenario(),
        generation=1,
        settings=settings,
        previous_best=0.5,
        challenger_elo=1000.0,
        score_history=[0.5],
        gate_decision_history=["advance"],
        coach_competitor_hints="",
        replay_narrative="",
    )
    defaults.update(overrides)
    return GenerationContext(**defaults)


def _make_artifacts() -> MagicMock:
    """Build a mock ArtifactStore."""
    artifacts = MagicMock()
    artifacts.read_playbook.return_value = "playbook content"
    artifacts.read_tool_context.return_value = ""
    artifacts.read_skills.return_value = ""
    artifacts.read_latest_advance_analysis.return_value = ""
    artifacts.read_hints.return_value = ""
    artifacts.read_tuning.return_value = ""
    artifacts.read_research_protocol.return_value = ""
    artifacts.read_progress.return_value = None
    return artifacts


def _make_trajectory_builder() -> MagicMock:
    """Build a mock ScoreTrajectoryBuilder."""
    tb = MagicMock()
    tb.build_trajectory.return_value = ""
    tb.build_strategy_registry.return_value = ""
    return tb


def _mock_prompt_bundle() -> MagicMock:
    """Build a mock PromptBundle."""
    bundle = MagicMock()
    bundle.competitor = "competitor prompt"
    bundle.analyst = "analyst prompt"
    bundle.coach = "coach prompt"
    bundle.architect = "architect prompt"
    return bundle


# ===========================================================================
# #185 - Load tuning.json at run start
# ===========================================================================


class TestTuningLoadAtStartup:
    """Tuning config should be loaded and applied in stage_knowledge_setup."""

    def test_tuning_loaded_when_config_adaptive_enabled(self) -> None:
        """When config_adaptive_enabled=True, stage_knowledge_setup reads tuning.json
        and applies validated parameters to ctx.settings."""
        from autocontext.loop.stages import stage_knowledge_setup

        tuning_data = TuningConfig(
            parameters={"matches_per_generation": 5, "backpressure_min_delta": 0.01},
        )
        settings = _make_settings(config_adaptive_enabled=True, matches_per_generation=3)
        ctx = _make_ctx(settings=settings)
        artifacts = _make_artifacts()
        artifacts.read_tuning.return_value = tuning_data.to_json()
        tb = _make_trajectory_builder()

        with patch("autocontext.loop.stage_helpers.semantic_benchmark.build_prompt_bundle", return_value=_mock_prompt_bundle()):
            result = stage_knowledge_setup(ctx, artifacts=artifacts, trajectory_builder=tb)

        artifacts.read_tuning.assert_called_once_with("grid_ctf")
        # Settings should be updated with tuning parameters
        assert result.settings.matches_per_generation == 5
        assert result.settings.backpressure_min_delta == 0.01

    def test_tuning_not_loaded_when_config_adaptive_disabled(self) -> None:
        """When config_adaptive_enabled=False, tuning.json should NOT be read."""
        from autocontext.loop.stages import stage_knowledge_setup

        settings = _make_settings(config_adaptive_enabled=False)
        ctx = _make_ctx(settings=settings)
        artifacts = _make_artifacts()
        tb = _make_trajectory_builder()

        with patch("autocontext.loop.stage_helpers.semantic_benchmark.build_prompt_bundle", return_value=_mock_prompt_bundle()):
            stage_knowledge_setup(ctx, artifacts=artifacts, trajectory_builder=tb)

        artifacts.read_tuning.assert_not_called()

    def test_tuning_empty_json_no_crash(self) -> None:
        """When tuning.json is empty, stage should not crash or change settings."""
        from autocontext.loop.stages import stage_knowledge_setup

        settings = _make_settings(config_adaptive_enabled=True, matches_per_generation=3)
        ctx = _make_ctx(settings=settings)
        artifacts = _make_artifacts()
        artifacts.read_tuning.return_value = ""
        tb = _make_trajectory_builder()

        with patch("autocontext.loop.stage_helpers.semantic_benchmark.build_prompt_bundle", return_value=_mock_prompt_bundle()):
            result = stage_knowledge_setup(ctx, artifacts=artifacts, trajectory_builder=tb)

        assert result.settings.matches_per_generation == 3  # unchanged


# ===========================================================================
# #166 - Apply protocol tuning overrides
# ===========================================================================


class TestProtocolOverrides:
    """Protocol tuning overrides should be applied in stage_knowledge_setup."""

    def test_protocol_overrides_applied_when_protocol_enabled(self) -> None:
        """When protocol_enabled=True, read protocol and apply tuning overrides."""
        from autocontext.loop.stages import stage_knowledge_setup

        protocol = ResearchProtocol(
            exploration_mode="rapid",
            tuning_overrides={"matches_per_generation": 7, "backpressure_min_delta": 0.02},
        )
        settings = _make_settings(
            protocol_enabled=True,
            matches_per_generation=3,
            exploration_mode="linear",
        )
        ctx = _make_ctx(settings=settings)
        artifacts = _make_artifacts()
        artifacts.read_research_protocol.return_value = protocol.to_markdown()
        tb = _make_trajectory_builder()

        with patch("autocontext.loop.stage_helpers.semantic_benchmark.build_prompt_bundle", return_value=_mock_prompt_bundle()):
            result = stage_knowledge_setup(ctx, artifacts=artifacts, trajectory_builder=tb)

        artifacts.read_research_protocol.assert_called_once_with("grid_ctf")
        assert result.settings.matches_per_generation == 7
        assert result.settings.backpressure_min_delta == 0.02
        assert result.settings.exploration_mode == "rapid"

    def test_protocol_overrides_not_applied_when_disabled(self) -> None:
        """When protocol_enabled=False, protocol should NOT be read."""
        from autocontext.loop.stages import stage_knowledge_setup

        settings = _make_settings(protocol_enabled=False)
        ctx = _make_ctx(settings=settings)
        artifacts = _make_artifacts()
        tb = _make_trajectory_builder()

        with patch("autocontext.loop.stage_helpers.semantic_benchmark.build_prompt_bundle", return_value=_mock_prompt_bundle()):
            stage_knowledge_setup(ctx, artifacts=artifacts, trajectory_builder=tb)

        artifacts.read_research_protocol.assert_not_called()

    def test_protocol_empty_no_crash(self) -> None:
        """When protocol file is empty, settings should not change."""
        from autocontext.loop.stages import stage_knowledge_setup

        settings = _make_settings(protocol_enabled=True, exploration_mode="linear")
        ctx = _make_ctx(settings=settings)
        artifacts = _make_artifacts()
        artifacts.read_research_protocol.return_value = ""
        tb = _make_trajectory_builder()

        with patch("autocontext.loop.stage_helpers.semantic_benchmark.build_prompt_bundle", return_value=_mock_prompt_bundle()):
            result = stage_knowledge_setup(ctx, artifacts=artifacts, trajectory_builder=tb)

        assert result.settings.exploration_mode == "linear"


# ===========================================================================
# #168 + #172 - Exploration mode + rapid gate in stage_tournament
# ===========================================================================


def _make_mock_tournament() -> MagicMock:
    """Build a mock tournament result usable in stage_tournament tests."""
    mock_eval_result = MagicMock()
    mock_eval_result.score = 0.6
    mock_exec_output = MagicMock()
    mock_exec_output.result.score = 0.6
    mock_exec_output.result.passed_validation = True
    mock_exec_output.result.validation_errors = []
    mock_exec_output.result.replay = MagicMock()
    mock_exec_output.replay = MagicMock()
    mock_eval_result.metadata = {"execution_output": mock_exec_output}

    mock_tournament = MagicMock()
    mock_tournament.results = [mock_eval_result]
    mock_tournament.best_score = 0.6
    mock_tournament.mean_score = 0.6
    mock_tournament.wins = 1
    mock_tournament.losses = 0
    mock_tournament.elo_after = 1010.0
    return mock_tournament


class TestRapidGateInTournament:
    """Rapid gate should be used in stage_tournament when exploration_mode='rapid'."""

    def test_rapid_gate_used_when_exploration_mode_rapid(self) -> None:
        """When exploration_mode='rapid', rapid_gate() is called instead of standard gate."""
        from autocontext.loop.stages import stage_tournament

        settings = _make_settings(
            exploration_mode="rapid",
            matches_per_generation=1,
            max_retries=0,
        )
        ctx = _make_ctx(settings=settings)
        ctx.outputs = MagicMock()
        ctx.current_strategy = {"move": "north"}

        supervisor = MagicMock()
        gate = MagicMock()  # Standard gate -- should NOT be used
        events = MagicMock()
        sqlite = MagicMock()
        artifacts = _make_artifacts()
        artifacts.generation_dir.return_value = MagicMock()

        mock_tournament = _make_mock_tournament()

        with patch("autocontext.loop.stages.EvaluationRunner") as MockRunner, \
             patch("autocontext.loop.stages.ScenarioEvaluator"), \
             patch("autocontext.loop.stages.rapid_gate") as mock_rapid_gate:
            MockRunner.return_value.run.return_value = mock_tournament
            mock_rapid_gate.return_value = RapidGateResult(
                decision="advance", delta=0.1, reason="improved",
            )

            result = stage_tournament(
                ctx,
                supervisor=supervisor,
                gate=gate,
                events=events,
                sqlite=sqlite,
                artifacts=artifacts,
            )

        # rapid_gate should have been called
        mock_rapid_gate.assert_called_once_with(0.6, 0.5)
        # Standard gate.evaluate should NOT have been called
        gate.evaluate.assert_not_called()
        assert result.gate_decision == "advance"

    def test_standard_gate_used_when_exploration_mode_linear(self) -> None:
        """When exploration_mode='linear', the standard gate is used (no rapid_gate)."""
        from autocontext.loop.stages import stage_tournament

        settings = _make_settings(
            exploration_mode="linear",
            matches_per_generation=1,
            max_retries=0,
        )
        ctx = _make_ctx(settings=settings)
        ctx.outputs = MagicMock()
        ctx.current_strategy = {"move": "north"}

        supervisor = MagicMock()
        gate = MagicMock()
        gate.evaluate.return_value = MagicMock(decision="advance", reason="ok")
        events = MagicMock()
        sqlite = MagicMock()
        artifacts = _make_artifacts()
        artifacts.generation_dir.return_value = MagicMock()

        mock_tournament = _make_mock_tournament()

        with patch("autocontext.loop.stages.EvaluationRunner") as MockRunner, \
             patch("autocontext.loop.stages.ScenarioEvaluator"), \
             patch("autocontext.loop.stages.rapid_gate") as mock_rapid_gate:
            MockRunner.return_value.run.return_value = mock_tournament

            stage_tournament(
                ctx,
                supervisor=supervisor,
                gate=gate,
                events=events,
                sqlite=sqlite,
                artifacts=artifacts,
            )

        # rapid_gate should NOT have been called
        mock_rapid_gate.assert_not_called()
        # Standard gate should have been called
        gate.evaluate.assert_called_once()


# ===========================================================================
# #173 - Auto-transition from rapid to linear
# ===========================================================================


class TestRapidToLinearTransition:
    """After rapid_gens generations in rapid mode, auto-transition to linear."""

    def test_transition_after_rapid_gens(self) -> None:
        """When generation >= rapid_gens, exploration_mode changes to 'linear'."""
        from autocontext.loop.stages import stage_tournament

        settings = _make_settings(
            exploration_mode="rapid",
            rapid_gens=3,
            matches_per_generation=1,
            max_retries=0,
        )
        ctx = _make_ctx(settings=settings, generation=3)
        ctx.outputs = MagicMock()
        ctx.current_strategy = {"move": "north"}

        supervisor = MagicMock()
        gate = MagicMock()
        events = MagicMock()
        sqlite = MagicMock()
        artifacts = _make_artifacts()
        artifacts.generation_dir.return_value = MagicMock()

        mock_tournament = _make_mock_tournament()

        with patch("autocontext.loop.stages.EvaluationRunner") as MockRunner, \
             patch("autocontext.loop.stages.ScenarioEvaluator"), \
             patch("autocontext.loop.stages.rapid_gate") as mock_rapid_gate, \
             patch("autocontext.loop.stages.should_transition_to_linear") as mock_transition:
            MockRunner.return_value.run.return_value = mock_tournament
            mock_rapid_gate.return_value = RapidGateResult(
                decision="advance", delta=0.1, reason="improved",
            )
            mock_transition.return_value = True

            result = stage_tournament(
                ctx,
                supervisor=supervisor,
                gate=gate,
                events=events,
                sqlite=sqlite,
                artifacts=artifacts,
            )

        mock_transition.assert_called_once_with(3, 3)
        assert result.settings.exploration_mode == "linear"

    def test_no_transition_before_rapid_gens(self) -> None:
        """When generation < rapid_gens, exploration_mode stays 'rapid'."""
        from autocontext.loop.stages import stage_tournament

        settings = _make_settings(
            exploration_mode="rapid",
            rapid_gens=5,
            matches_per_generation=1,
            max_retries=0,
        )
        ctx = _make_ctx(settings=settings, generation=2)
        ctx.outputs = MagicMock()
        ctx.current_strategy = {"move": "north"}

        supervisor = MagicMock()
        gate = MagicMock()
        events = MagicMock()
        sqlite = MagicMock()
        artifacts = _make_artifacts()
        artifacts.generation_dir.return_value = MagicMock()

        mock_tournament = _make_mock_tournament()

        with patch("autocontext.loop.stages.EvaluationRunner") as MockRunner, \
             patch("autocontext.loop.stages.ScenarioEvaluator"), \
             patch("autocontext.loop.stages.rapid_gate") as mock_rapid_gate, \
             patch("autocontext.loop.stages.should_transition_to_linear") as mock_transition:
            MockRunner.return_value.run.return_value = mock_tournament
            mock_rapid_gate.return_value = RapidGateResult(
                decision="advance", delta=0.1, reason="improved",
            )
            mock_transition.return_value = False

            result = stage_tournament(
                ctx,
                supervisor=supervisor,
                gate=gate,
                events=events,
                sqlite=sqlite,
                artifacts=artifacts,
            )

        assert result.settings.exploration_mode == "rapid"


# ===========================================================================
# #186 - Wire tuning analysis from architect
# ===========================================================================


class TestTuningFromArchitect:
    """Tuning proposals parsed from architect output in stage_agent_generation."""

    def test_tuning_proposal_parsed_when_config_adaptive_enabled(self) -> None:
        """When config_adaptive_enabled=True and architect output contains a tuning
        proposal, it should be parsed and stored on ctx."""
        from autocontext.loop.stages import stage_agent_generation

        settings = _make_settings(config_adaptive_enabled=True)
        ctx = _make_ctx(settings=settings)
        ctx.prompts = MagicMock()

        architect_md = (
            "Some architect output\n"
            "<!-- TUNING_PROPOSAL_START -->\n"
            '{"matches_per_generation": 5, "backpressure_min_delta": 0.02, "reasoning": "test"}\n'
            "<!-- TUNING_PROPOSAL_END -->\n"
        )

        orchestrator = MagicMock()
        outputs = MagicMock()
        outputs.strategy = {"move": "north"}
        outputs.analysis_markdown = "analysis"
        outputs.coach_markdown = "coach"
        outputs.architect_markdown = architect_md
        outputs.architect_tools = []
        outputs.role_executions = []
        orchestrator.run_generation.return_value = outputs

        artifacts = _make_artifacts()
        artifacts.persist_tools.return_value = []
        sqlite = MagicMock()

        with patch("autocontext.loop.stages.parse_dag_changes", return_value=[]):
            result = stage_agent_generation(
                ctx,
                orchestrator=orchestrator,
                artifacts=artifacts,
                sqlite=sqlite,
            )

        assert result.tuning_proposal is not None
        assert result.tuning_proposal.parameters["matches_per_generation"] == 5

    def test_tuning_proposal_not_parsed_when_config_adaptive_disabled(self) -> None:
        """When config_adaptive_enabled=False, tuning proposals should NOT be parsed."""
        from autocontext.loop.stages import stage_agent_generation

        settings = _make_settings(config_adaptive_enabled=False)
        ctx = _make_ctx(settings=settings)
        ctx.prompts = MagicMock()

        architect_md = (
            "<!-- TUNING_PROPOSAL_START -->\n"
            '{"matches_per_generation": 5, "reasoning": "test"}\n'
            "<!-- TUNING_PROPOSAL_END -->\n"
        )

        orchestrator = MagicMock()
        outputs = MagicMock()
        outputs.strategy = {"move": "north"}
        outputs.analysis_markdown = "analysis"
        outputs.coach_markdown = "coach"
        outputs.architect_markdown = architect_md
        outputs.architect_tools = []
        outputs.role_executions = []
        orchestrator.run_generation.return_value = outputs

        artifacts = _make_artifacts()
        artifacts.persist_tools.return_value = []
        sqlite = MagicMock()

        with patch("autocontext.loop.stages.parse_dag_changes", return_value=[]):
            result = stage_agent_generation(
                ctx,
                orchestrator=orchestrator,
                artifacts=artifacts,
                sqlite=sqlite,
            )

        assert result.tuning_proposal is None


# ===========================================================================
# #188 - Curator gate / persistence for tuning proposals
# ===========================================================================


class TestTuningPersistence:
    """Tuning proposals should be persisted on advance, not on rollback."""

    def _make_persistence_ctx(
        self, gate_decision: str, tuning: TuningConfig | None,
    ) -> GenerationContext:
        """Build a GenerationContext ready for stage_persistence testing."""
        settings = _make_settings(config_adaptive_enabled=True)
        ctx = _make_ctx(settings=settings)
        ctx.gate_decision = gate_decision
        ctx.gate_delta = 0.1 if gate_decision == "advance" else -0.1
        ctx.tuning_proposal = tuning
        ctx.attempt = 0

        mock_eval_result = MagicMock()
        mock_eval_result.score = 0.6 if gate_decision == "advance" else 0.4
        mock_exec_output = MagicMock()
        mock_exec_output.result.score = mock_eval_result.score
        mock_exec_output.result.passed_validation = True
        mock_exec_output.result.validation_errors = []
        mock_exec_output.result.replay = MagicMock()
        mock_exec_output.replay = MagicMock()
        mock_eval_result.metadata = {"execution_output": mock_exec_output}

        ctx.tournament = MagicMock()
        ctx.tournament.results = [mock_eval_result]
        ctx.tournament.mean_score = mock_eval_result.score
        ctx.tournament.best_score = mock_eval_result.score
        ctx.tournament.wins = 1 if gate_decision == "advance" else 0
        ctx.tournament.losses = 0 if gate_decision == "advance" else 1
        ctx.tournament.elo_after = 1010.0

        ctx.outputs = MagicMock()
        ctx.outputs.coach_playbook = ""
        ctx.outputs.coach_lessons = "lessons"
        ctx.outputs.analysis_markdown = "analysis"
        ctx.outputs.coach_markdown = "coach"
        ctx.outputs.architect_markdown = "architect"
        ctx.outputs.coach_competitor_hints = ""
        ctx.current_strategy = {"move": "north"}

        return ctx

    def test_tuning_persisted_on_advance(self) -> None:
        """When gate_decision='advance' and tuning_proposal exists, persist it."""
        from autocontext.loop.stages import stage_persistence

        tuning = TuningConfig(
            parameters={"matches_per_generation": 5},
            reasoning="test",
        )
        ctx = self._make_persistence_ctx("advance", tuning)

        artifacts = _make_artifacts()
        artifacts.generation_dir.return_value = MagicMock()
        artifacts.persist_generation.return_value = None
        artifacts.persist_skill_note.return_value = None
        artifacts.read_skill_lessons_raw.return_value = []
        sqlite = MagicMock()
        tb = _make_trajectory_builder()
        events = MagicMock()

        stage_persistence(
            ctx,
            artifacts=artifacts,
            sqlite=sqlite,
            trajectory_builder=tb,
            events=events,
            curator=None,
        )

        artifacts.write_tuning.assert_called_once_with("grid_ctf", tuning.to_json())

    def test_tuning_not_persisted_on_rollback(self) -> None:
        """When gate_decision='rollback', tuning proposals should NOT be persisted."""
        from autocontext.loop.stages import stage_persistence

        tuning = TuningConfig(
            parameters={"matches_per_generation": 5},
            reasoning="test",
        )
        ctx = self._make_persistence_ctx("rollback", tuning)

        artifacts = _make_artifacts()
        artifacts.generation_dir.return_value = MagicMock()
        artifacts.persist_generation.return_value = None
        artifacts.persist_skill_note.return_value = None
        artifacts.read_skill_lessons_raw.return_value = []
        sqlite = MagicMock()
        tb = _make_trajectory_builder()
        events = MagicMock()

        stage_persistence(
            ctx,
            artifacts=artifacts,
            sqlite=sqlite,
            trajectory_builder=tb,
            events=events,
            curator=None,
        )

        artifacts.write_tuning.assert_not_called()

    def test_no_tuning_proposal_no_persistence(self) -> None:
        """When tuning_proposal is None, write_tuning should not be called."""
        from autocontext.loop.stages import stage_persistence

        ctx = self._make_persistence_ctx("advance", None)

        artifacts = _make_artifacts()
        artifacts.generation_dir.return_value = MagicMock()
        artifacts.persist_generation.return_value = None
        artifacts.persist_skill_note.return_value = None
        artifacts.read_skill_lessons_raw.return_value = []
        sqlite = MagicMock()
        tb = _make_trajectory_builder()
        events = MagicMock()

        stage_persistence(
            ctx,
            artifacts=artifacts,
            sqlite=sqlite,
            trajectory_builder=tb,
            events=events,
            curator=None,
        )

        artifacts.write_tuning.assert_not_called()
