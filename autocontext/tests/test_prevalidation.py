"""Tests for Strategy Pre-Validation (P1) — dry-run self-play before tournament.

Tests cover:
1. ValidationResult and StrategyValidator (execution/strategy_validator.py)
2. Config fields for prevalidation
3. CompetitorRunner.revise() method
4. Pipeline integration (stage_prevalidation)
5. Event emission
6. Pipeline wiring (GenerationPipeline calls stage_prevalidation)
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from autocontext.config.settings import AppSettings, load_settings
from autocontext.scenarios.base import Result

# ---------------------------------------------------------------------------
# Helpers: Fake scenario for testing
# ---------------------------------------------------------------------------


class FakeScenario:
    """Minimal scenario that supports execute_match and validate_actions."""

    name = "fake_scenario"

    def __init__(
        self,
        *,
        match_result: Result | None = None,
        match_exception: Exception | None = None,
        validate_ok: bool = True,
        validate_reason: str = "",
    ) -> None:
        self._match_result = match_result
        self._match_exception = match_exception
        self._validate_ok = validate_ok
        self._validate_reason = validate_reason

    def execute_match(self, strategy: Mapping[str, Any], seed: int) -> Result:
        if self._match_exception is not None:
            raise self._match_exception
        if self._match_result is not None:
            return self._match_result
        return Result(score=0.5, summary="default match", validation_errors=[])

    def initial_state(self, seed: int | None = None) -> dict[str, Any]:
        return {"grid": [[0]], "seed": seed}

    def validate_actions(
        self, state: Mapping[str, Any], player_id: str, actions: Mapping[str, Any],
    ) -> tuple[bool, str]:
        return self._validate_ok, self._validate_reason


# ---------------------------------------------------------------------------
# 1. ValidationResult basics
# ---------------------------------------------------------------------------


class TestValidationResult:
    def test_valid_json_strategy_passes(self) -> None:
        """execute_match succeeds -> passed=True, no errors."""
        from autocontext.execution.strategy_validator import StrategyValidator

        scenario = FakeScenario(
            match_result=Result(score=0.7, summary="good match", validation_errors=[]),
        )
        settings = AppSettings()
        validator = StrategyValidator(scenario, settings)
        result = validator.validate({"aggression": 0.5})

        assert result.passed is True
        assert result.errors == []
        assert "good match" in result.match_summary

    def test_invalid_json_strategy_detected(self) -> None:
        """execute_match raises exception -> passed=False with error traceback."""
        from autocontext.execution.strategy_validator import StrategyValidator

        scenario = FakeScenario(match_exception=RuntimeError("kaboom: invalid move"))
        settings = AppSettings()
        validator = StrategyValidator(scenario, settings)
        result = validator.validate({"aggression": 99})

        assert result.passed is False
        assert len(result.errors) >= 1
        assert "kaboom" in result.errors[0]

    def test_validation_errors_in_result(self) -> None:
        """Result.validation_errors populated -> passed=False."""
        from autocontext.execution.strategy_validator import StrategyValidator

        scenario = FakeScenario(
            match_result=Result(
                score=0.0,
                summary="failed validation",
                validation_errors=["out of range", "missing field"],
            ),
        )
        settings = AppSettings()
        validator = StrategyValidator(scenario, settings)
        result = validator.validate({"aggression": 0.5})

        assert result.passed is False
        assert "out of range" in result.errors
        assert "missing field" in result.errors

    def test_code_strategy_passthrough(self) -> None:
        """__code__ strategies skip dry-run -> passed=True."""
        from autocontext.execution.strategy_validator import StrategyValidator

        scenario = FakeScenario(match_exception=RuntimeError("should not be called"))
        settings = AppSettings()
        validator = StrategyValidator(scenario, settings)
        result = validator.validate({"__code__": "print('hello')"})

        assert result.passed is True
        assert result.errors == []


# ---------------------------------------------------------------------------
# 2. format_revision_prompt
# ---------------------------------------------------------------------------


class TestFormatRevisionPrompt:
    def test_format_revision_prompt_includes_errors(self) -> None:
        """Revision prompt must contain error details."""
        from autocontext.execution.strategy_validator import StrategyValidator, ValidationResult

        scenario = FakeScenario()
        settings = AppSettings()
        validator = StrategyValidator(scenario, settings)
        vr = ValidationResult(passed=False, errors=["index out of bounds", "negative score"])
        prompt = validator.format_revision_prompt(vr, {"aggression": 99})

        assert "index out of bounds" in prompt
        assert "negative score" in prompt

    def test_format_revision_prompt_includes_strategy(self) -> None:
        """Revision prompt must show the original strategy."""
        from autocontext.execution.strategy_validator import StrategyValidator, ValidationResult

        scenario = FakeScenario()
        settings = AppSettings()
        validator = StrategyValidator(scenario, settings)
        strategy = {"aggression": 0.8, "defense": 0.2}
        vr = ValidationResult(passed=False, errors=["bad move"])
        prompt = validator.format_revision_prompt(vr, strategy)

        assert "aggression" in prompt
        assert "0.8" in prompt


# ---------------------------------------------------------------------------
# 3. Config fields
# ---------------------------------------------------------------------------


class TestConfigFields:
    def test_config_defaults(self) -> None:
        """prevalidation_enabled defaults to False, max_retries defaults to 2, dry_run defaults True."""
        settings = AppSettings()
        assert settings.prevalidation_enabled is False
        assert settings.prevalidation_max_retries == 2
        assert settings.prevalidation_dry_run_enabled is True

    def test_config_env_vars(self) -> None:
        """AUTOCONTEXT_PREVALIDATION_ENABLED=true loads correctly."""
        with patch.dict(
            "os.environ",
            {
                "AUTOCONTEXT_PREVALIDATION_ENABLED": "true",
                "AUTOCONTEXT_PREVALIDATION_MAX_RETRIES": "3",
            },
        ):
            settings = load_settings()
            assert settings.prevalidation_enabled is True
            assert settings.prevalidation_max_retries == 3

    def test_dry_run_toggle_env_var(self) -> None:
        """AUTOCONTEXT_PREVALIDATION_DRY_RUN_ENABLED=false disables self-play dry-run."""
        with patch.dict(
            "os.environ",
            {
                "AUTOCONTEXT_PREVALIDATION_DRY_RUN_ENABLED": "false",
            },
        ):
            settings = load_settings()
            assert settings.prevalidation_dry_run_enabled is False


# ---------------------------------------------------------------------------
# 4. CompetitorRunner.revise()
# ---------------------------------------------------------------------------


class TestCompetitorRevise:
    def test_competitor_revise_method(self) -> None:
        """revise() calls run() with combined prompt including revision feedback."""
        from autocontext.agents.competitor import CompetitorRunner
        from autocontext.agents.types import RoleExecution
        from autocontext.harness.core.types import RoleUsage

        mock_runtime = MagicMock()
        mock_exec = RoleExecution(
            role="competitor",
            content="revised strategy output",
            usage=RoleUsage(input_tokens=10, output_tokens=20, latency_ms=100, model="test"),
            subagent_id="test",
            status="completed",
        )
        mock_runtime.run_task.return_value = mock_exec

        runner = CompetitorRunner(mock_runtime, "test-model")
        result_text, _ = runner.revise(
            original_prompt="Generate a strategy",
            revision_prompt="Fix the error: index out of bounds",
            tool_context="some tools",
        )

        assert result_text == "revised strategy output"
        # Verify the prompt contains both original and revision
        call_args = mock_runtime.run_task.call_args
        task = call_args[0][0]
        assert "Generate a strategy" in task.prompt
        assert "REVISION REQUIRED" in task.prompt
        assert "index out of bounds" in task.prompt
        assert "some tools" in task.prompt


# ---------------------------------------------------------------------------
# 5. Pipeline stage: stage_prevalidation
# ---------------------------------------------------------------------------


class TestStagePrevalidation:
    def _make_ctx(
        self,
        *,
        prevalidation_enabled: bool = True,
        max_retries: int = 2,
        dry_run_enabled: bool = True,
    ) -> Any:
        """Build a minimal GenerationContext for testing."""
        from autocontext.loop.stage_types import GenerationContext

        settings = AppSettings(
            prevalidation_enabled=prevalidation_enabled,
            prevalidation_max_retries=max_retries,
            prevalidation_dry_run_enabled=dry_run_enabled,
        )
        scenario = FakeScenario(
            match_result=Result(score=0.5, summary="ok", validation_errors=[]),
        )
        return GenerationContext(
            run_id="test-run",
            scenario_name="fake",
            scenario=scenario,  # type: ignore[arg-type]
            generation=1,
            settings=settings,
            previous_best=0.0,
            challenger_elo=1500.0,
            score_history=[],
            gate_decision_history=[],
            coach_competitor_hints="",
            replay_narrative="",
            current_strategy={"aggression": 0.5},
            strategy_interface='{"aggression": float}',
        )

    def test_pipeline_skips_when_disabled(self) -> None:
        """When prevalidation_enabled=False, stage returns ctx unchanged."""
        from autocontext.loop.stage_prevalidation import stage_prevalidation

        ctx = self._make_ctx(prevalidation_enabled=False)
        events = MagicMock()
        agents = MagicMock()

        result = stage_prevalidation(ctx, events=events, agents=agents)

        assert result is ctx
        events.emit.assert_not_called()

    def test_pipeline_passes_valid_strategy(self) -> None:
        """When strategy validates, stage succeeds with no revision."""
        from autocontext.loop.stage_prevalidation import stage_prevalidation

        ctx = self._make_ctx()
        events = MagicMock()
        agents = MagicMock()

        result = stage_prevalidation(ctx, events=events, agents=agents)

        assert result is ctx
        # Should emit dry_run started + passed events
        event_names = [call[0][0] for call in events.emit.call_args_list]
        assert "dry_run_started" in event_names
        assert "dry_run_passed" in event_names

    def test_pipeline_retries_on_failure(self) -> None:
        """When validation fails, stage calls competitor.revise() and retries."""
        from autocontext.loop.stage_prevalidation import stage_prevalidation

        ctx = self._make_ctx(max_retries=1)
        # Make scenario fail first time, pass second time
        call_count = {"n": 0}

        def _execute_match(strategy: Mapping[str, Any], seed: int) -> Result:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("bad strategy")
            return Result(score=0.5, summary="ok", validation_errors=[])

        ctx.scenario.execute_match = _execute_match  # type: ignore[assignment]

        events = MagicMock()
        agents = MagicMock()

        # Mock competitor.revise to return a new strategy
        from autocontext.agents.types import RoleExecution
        from autocontext.harness.core.types import RoleUsage

        mock_exec = RoleExecution(
            role="competitor", content='{"aggression": 0.3}',
            usage=RoleUsage(input_tokens=10, output_tokens=20, latency_ms=100, model="test"),
            subagent_id="test", status="completed",
        )
        agents.competitor.revise.return_value = ('{"aggression": 0.3}', mock_exec)
        agents.translator.translate.return_value = ({"aggression": 0.3}, mock_exec)

        stage_prevalidation(ctx, events=events, agents=agents)

        # Should have emitted failed + revision events
        event_names = [call[0][0] for call in events.emit.call_args_list]
        assert "dry_run_failed" in event_names
        assert "dry_run_revision" in event_names
        # And eventually passed
        assert "dry_run_passed" in event_names

    def test_dry_run_disabled_skips_self_play(self) -> None:
        """When dry_run_enabled=False, skip self-play but still run harness."""
        from autocontext.loop.stage_prevalidation import stage_prevalidation

        ctx = self._make_ctx(dry_run_enabled=False)
        events = MagicMock()
        agents = MagicMock()

        harness_loader = MagicMock()
        harness_loader.validate_strategy.return_value = MagicMock(passed=True, errors=[])

        result = stage_prevalidation(ctx, events=events, agents=agents, harness_loader=harness_loader)

        assert result is ctx
        event_names = [call[0][0] for call in events.emit.call_args_list]
        # Harness phase should run
        assert "harness_validation_started" in event_names
        assert "harness_validation_passed" in event_names
        # Dry-run phase should NOT run
        assert "dry_run_started" not in event_names

    def test_dry_run_disabled_no_harness_returns_immediately(self) -> None:
        """With dry_run disabled and no harness loader, stage is a no-op."""
        from autocontext.loop.stage_prevalidation import stage_prevalidation

        ctx = self._make_ctx(dry_run_enabled=False)
        events = MagicMock()
        agents = MagicMock()

        result = stage_prevalidation(ctx, events=events, agents=agents, harness_loader=None)

        assert result is ctx
        event_names = [call[0][0] for call in events.emit.call_args_list]
        assert "dry_run_started" not in event_names
        assert "harness_validation_started" not in event_names

    def test_max_retries_exhaustion(self) -> None:
        """After N failures, falls through to tournament with last strategy."""
        from autocontext.loop.stage_prevalidation import stage_prevalidation

        ctx = self._make_ctx(max_retries=1)
        # Always fail
        ctx.scenario = FakeScenario(match_exception=RuntimeError("always fails"))  # type: ignore[assignment]

        events = MagicMock()
        agents = MagicMock()

        # Mock competitor.revise
        from autocontext.agents.types import RoleExecution
        from autocontext.harness.core.types import RoleUsage

        mock_exec = RoleExecution(
            role="competitor", content='{"aggression": 0.3}',
            usage=RoleUsage(input_tokens=10, output_tokens=20, latency_ms=100, model="test"),
            subagent_id="test", status="completed",
        )
        agents.competitor.revise.return_value = ('{"aggression": 0.3}', mock_exec)
        agents.translator.translate.return_value = ({"aggression": 0.3}, mock_exec)

        ctx_out = stage_prevalidation(ctx, events=events, agents=agents)

        # Should still return ctx (fall through)
        assert ctx_out is ctx
        # Check exhaustion event was emitted
        event_names = [call[0][0] for call in events.emit.call_args_list]
        assert "dry_run_failed" in event_names

    def test_regression_fixtures_trigger_revision_before_dry_run(self) -> None:
        """Persisted regression fixtures participate in the live prevalidation loop."""
        from autocontext.analytics.regression_fixtures import RegressionFixture
        from autocontext.harness.evaluation.types import EvaluationResult
        from autocontext.loop.stage_prevalidation import stage_prevalidation

        ctx = self._make_ctx(max_retries=1)
        events = MagicMock()
        agents = MagicMock()
        artifacts = MagicMock()
        artifacts.knowledge_root = Path("/tmp/knowledge")
        supervisor = MagicMock()

        fixture = RegressionFixture(
            fixture_id="fix-fake-rollback",
            scenario="fake",
            description="Regression fixture for rollback",
            seed=101,
            strategy={},
            expected_min_score=0.5,
            source_evidence=["friction:rollback:gen2"],
            confidence=0.9,
        )
        fake_store = MagicMock()
        fake_store.list_for_scenario.return_value = [fixture]
        fake_evaluator = MagicMock()
        fake_evaluator.evaluate.side_effect = [
            EvaluationResult(score=0.2, passed=True),
            EvaluationResult(score=0.8, passed=True),
        ]

        from autocontext.agents.types import RoleExecution
        from autocontext.harness.core.types import RoleUsage

        mock_exec = RoleExecution(
            role="competitor", content='{"aggression": 0.3}',
            usage=RoleUsage(input_tokens=10, output_tokens=20, latency_ms=100, model="test"),
            subagent_id="test", status="completed",
        )
        agents.competitor.revise.return_value = ('{"aggression": 0.3}', mock_exec)
        agents.translator.translate.return_value = ({"aggression": 0.3}, mock_exec)

        with (
            patch("autocontext.loop.stage_prevalidation.FixtureStore", return_value=fake_store),
            patch("autocontext.loop.stage_prevalidation.ScenarioEvaluator", return_value=fake_evaluator),
        ):
            stage_prevalidation(
                ctx,
                events=events,
                agents=agents,
                artifacts=artifacts,
                supervisor=supervisor,
            )

        event_names = [call[0][0] for call in events.emit.call_args_list]
        assert "regression_fixtures_started" in event_names
        assert "regression_fixtures_failed" in event_names
        assert "regression_fixtures_revision" in event_names
        assert "regression_fixtures_passed" in event_names
        assert "dry_run_started" in event_names


# ---------------------------------------------------------------------------
# 6. Event emission detail
# ---------------------------------------------------------------------------


class TestEventEmission:
    def test_event_emission_payloads(self) -> None:
        """Verify correct event payloads are emitted at each stage."""
        from autocontext.loop.stage_prevalidation import stage_prevalidation
        from autocontext.loop.stage_types import GenerationContext

        settings = AppSettings(
            prevalidation_enabled=True,
            prevalidation_max_retries=0,  # no retries, just validate once
        )
        scenario = FakeScenario(
            match_result=Result(score=0.8, summary="great match", validation_errors=[]),
        )
        ctx = GenerationContext(
            run_id="event-run",
            scenario_name="fake",
            scenario=scenario,  # type: ignore[arg-type]
            generation=3,
            settings=settings,
            previous_best=0.0,
            challenger_elo=1500.0,
            score_history=[],
            gate_decision_history=[],
            coach_competitor_hints="",
            replay_narrative="",
            current_strategy={"aggression": 0.5},
            strategy_interface='{"aggression": float}',
        )

        events = MagicMock()
        agents = MagicMock()

        stage_prevalidation(ctx, events=events, agents=agents)

        # Check started event payload
        started_calls = [
            call for call in events.emit.call_args_list
            if call[0][0] == "dry_run_started"
        ]
        assert len(started_calls) == 1
        payload = started_calls[0][0][1]
        assert payload["generation"] == 3

        # Check passed event payload
        passed_calls = [
            call for call in events.emit.call_args_list
            if call[0][0] == "dry_run_passed"
        ]
        assert len(passed_calls) == 1
        payload = passed_calls[0][0][1]
        assert payload["generation"] == 3
        assert payload["attempt"] == 0


# ---------------------------------------------------------------------------
# 7. Pipeline wiring — GenerationPipeline calls stage_prevalidation
# ---------------------------------------------------------------------------


class TestPipelineWiring:
    def test_generation_pipeline_imports_stage_prevalidation(self) -> None:
        """Verify GenerationPipeline module imports stage_prevalidation."""
        import autocontext.loop.generation_pipeline as gp

        assert hasattr(gp, "stage_prevalidation"), (
            "generation_pipeline must import stage_prevalidation"
        )

    def test_stage_prevalidation_is_patchable(self) -> None:
        """Verify stage_prevalidation can be patched in the pipeline module."""
        with patch("autocontext.loop.generation_pipeline.stage_prevalidation") as mock_stage:
            mock_stage.side_effect = lambda ctx, **kw: ctx
            # If we get here without error, the import exists and is patchable
            assert mock_stage is not None


# ---------------------------------------------------------------------------
# 8. Dead-end recording from pre-validation failures (MTS-107)
# ---------------------------------------------------------------------------


class TestDeadEndFromPrevalidation:
    def _make_ctx(
        self,
        *,
        max_retries: int = 1,
        dead_end_tracking_enabled: bool = True,
    ) -> Any:
        from autocontext.loop.stage_types import GenerationContext

        settings = AppSettings(
            prevalidation_enabled=True,
            prevalidation_max_retries=max_retries,
            prevalidation_dry_run_enabled=True,
            dead_end_tracking_enabled=dead_end_tracking_enabled,
        )
        scenario = FakeScenario(match_exception=RuntimeError("always fails"))
        return GenerationContext(
            run_id="test-run",
            scenario_name="fake",
            scenario=scenario,  # type: ignore[arg-type]
            generation=5,
            settings=settings,
            previous_best=0.0,
            challenger_elo=1500.0,
            score_history=[],
            gate_decision_history=[],
            coach_competitor_hints="",
            replay_narrative="",
            current_strategy={"aggression": 0.5},
            strategy_interface='{"aggression": float}',
        )

    def _mock_agents(self) -> MagicMock:
        from autocontext.agents.types import RoleExecution
        from autocontext.harness.core.types import RoleUsage

        agents = MagicMock()
        mock_exec = RoleExecution(
            role="competitor", content='{"aggression": 0.3}',
            usage=RoleUsage(input_tokens=10, output_tokens=20, latency_ms=100, model="test"),
            subagent_id="test", status="completed",
        )
        agents.competitor.revise.return_value = ('{"aggression": 0.3}', mock_exec)
        agents.translator.translate.return_value = ({"aggression": 0.3}, mock_exec)
        return agents

    def test_dry_run_exhaustion_records_dead_end(self) -> None:
        """When dry-run retries are exhausted, a dead end is recorded."""
        from autocontext.loop.stage_prevalidation import stage_prevalidation

        ctx = self._make_ctx(max_retries=1)
        events = MagicMock()
        agents = self._mock_agents()
        artifacts = MagicMock()

        stage_prevalidation(ctx, events=events, agents=agents, artifacts=artifacts)

        artifacts.append_dead_end.assert_called_once()
        call_args = artifacts.append_dead_end.call_args
        assert call_args[0][0] == "fake"  # scenario_name
        entry_text = call_args[0][1]
        assert "Gen 5" in entry_text
        assert "Pre-validation failed" in entry_text
        assert "score=0.0000" in entry_text

    def test_no_dead_end_when_tracking_disabled(self) -> None:
        """When dead_end_tracking_enabled=False, no dead end is recorded."""
        from autocontext.loop.stage_prevalidation import stage_prevalidation

        ctx = self._make_ctx(max_retries=0, dead_end_tracking_enabled=False)
        events = MagicMock()
        agents = self._mock_agents()
        artifacts = MagicMock()

        stage_prevalidation(ctx, events=events, agents=agents, artifacts=artifacts)

        artifacts.append_dead_end.assert_not_called()

    def test_no_dead_end_when_no_artifacts(self) -> None:
        """When artifacts is None, dead end recording is gracefully skipped."""
        from autocontext.loop.stage_prevalidation import stage_prevalidation

        ctx = self._make_ctx(max_retries=0)
        events = MagicMock()
        agents = self._mock_agents()

        # Should not raise even without artifacts
        stage_prevalidation(ctx, events=events, agents=agents, artifacts=None)

    def test_no_dead_end_when_validation_passes(self) -> None:
        """When strategy validates, no dead end is recorded."""
        from autocontext.loop.stage_prevalidation import stage_prevalidation

        ctx = self._make_ctx()
        ctx.scenario = FakeScenario(  # type: ignore[assignment]
            match_result=Result(score=0.5, summary="ok", validation_errors=[]),
        )
        events = MagicMock()
        agents = self._mock_agents()
        artifacts = MagicMock()

        stage_prevalidation(ctx, events=events, agents=agents, artifacts=artifacts)

        artifacts.append_dead_end.assert_not_called()

    def test_harness_failure_records_dead_end(self) -> None:
        """When harness validation exhausts retries, a dead end is recorded."""
        from autocontext.loop.stage_prevalidation import stage_prevalidation

        ctx = self._make_ctx(max_retries=0)
        ctx.settings = ctx.settings.model_copy(update={"prevalidation_dry_run_enabled": False})
        events = MagicMock()
        agents = self._mock_agents()
        artifacts = MagicMock()

        harness_loader = MagicMock()
        harness_loader.validate_strategy.return_value = MagicMock(
            passed=False, errors=["invalid move pattern"],
        )

        stage_prevalidation(
            ctx, events=events, agents=agents,
            harness_loader=harness_loader, artifacts=artifacts,
        )

        artifacts.append_dead_end.assert_called_once()
        entry_text = artifacts.append_dead_end.call_args[0][1]
        assert "Harness validation failed" in entry_text
        assert "invalid move pattern" in entry_text

    def test_harness_failure_without_errors_still_records_dead_end(self) -> None:
        """Harness failures with empty errors should not crash dead-end recording."""
        from autocontext.loop.stage_prevalidation import stage_prevalidation

        ctx = self._make_ctx(max_retries=0)
        ctx.settings = ctx.settings.model_copy(update={"prevalidation_dry_run_enabled": False})
        events = MagicMock()
        agents = self._mock_agents()
        artifacts = MagicMock()

        harness_loader = MagicMock()
        harness_loader.validate_strategy.return_value = MagicMock(passed=False, errors=[])

        stage_prevalidation(
            ctx, events=events, agents=agents,
            harness_loader=harness_loader, artifacts=artifacts,
        )

        artifacts.append_dead_end.assert_called_once()
        entry_text = artifacts.append_dead_end.call_args[0][1]
        assert "Harness validation failed after 0 revisions" in entry_text
