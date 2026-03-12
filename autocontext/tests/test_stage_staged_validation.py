"""Tests for AC-200: Integrate staged validation into harness pipeline.

Tests the stage_staged_validation function, config flag, context field
propagation, event emission, and early gate override on failure.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from unittest.mock import MagicMock

from autocontext.config.settings import AppSettings
from autocontext.harness.validation import StageStatus
from autocontext.loop.stage_types import GenerationContext

# ── Helpers ─────────────────────────────────────────────────────────────


class FakeScenario:
    """Minimal scenario stub for staged validation tests."""

    name = "fake_scenario"

    def __init__(
        self,
        *,
        validate_ok: bool = True,
        validate_reason: str = "",
    ) -> None:
        self._validate_ok = validate_ok
        self._validate_reason = validate_reason

    def initial_state(self, seed: int | None = None) -> dict[str, Any]:
        return {"grid": [[0]], "seed": seed}

    def validate_actions(
        self, state: Mapping[str, Any], player_id: str, actions: Mapping[str, Any],
    ) -> tuple[bool, str]:
        return self._validate_ok, self._validate_reason


def _make_ctx(
    *,
    strategy: dict[str, Any] | None = None,
    staged_validation_enabled: bool = True,
    scenario: Any = None,
) -> GenerationContext:
    """Build a minimal GenerationContext for testing."""
    settings = AppSettings(staged_validation_enabled=staged_validation_enabled)
    return GenerationContext(
        run_id="test-run",
        scenario_name="fake_scenario",
        scenario=scenario or FakeScenario(),
        generation=1,
        settings=settings,
        previous_best=0.0,
        challenger_elo=1000.0,
        score_history=[],
        gate_decision_history=[],
        coach_competitor_hints="",
        replay_narrative="",
        current_strategy=strategy or {"action": "move", "x": 1},
    )


def _make_events() -> MagicMock:
    """Create a mock EventStreamEmitter."""
    return MagicMock()


def _make_sqlite() -> MagicMock:
    """Create a mock SQLiteStore."""
    return MagicMock()


# ── Config flag tests ───────────────────────────────────────────────────


class TestStagedValidationConfig:
    def test_config_field_exists(self) -> None:
        settings = AppSettings()
        assert hasattr(settings, "staged_validation_enabled")

    def test_config_defaults_to_true(self) -> None:
        settings = AppSettings()
        assert settings.staged_validation_enabled is True

    def test_config_can_be_disabled(self) -> None:
        settings = AppSettings(staged_validation_enabled=False)
        assert settings.staged_validation_enabled is False


# ── Context field tests ─────────────────────────────────────────────────


class TestContextFields:
    def test_context_has_staged_validation_results_field(self) -> None:
        ctx = _make_ctx()
        assert ctx.staged_validation_results is None

    def test_context_has_staged_validation_metrics_field(self) -> None:
        ctx = _make_ctx()
        assert ctx.staged_validation_metrics is None


# ── Stage function tests ────────────────────────────────────────────────


class TestStageStagedValidation:
    def test_disabled_returns_ctx_unchanged(self) -> None:
        """When staged_validation_enabled=False, stage is a no-op."""
        from autocontext.loop.stage_staged_validation import stage_staged_validation

        ctx = _make_ctx(staged_validation_enabled=False)
        events = _make_events()
        result = stage_staged_validation(ctx, events=events, sqlite=_make_sqlite())
        assert result is ctx
        assert result.staged_validation_results is None
        events.emit.assert_not_called()

    def test_valid_dict_strategy_passes_all_stages(self) -> None:
        """A valid dict strategy should pass syntax and contract stages."""
        from autocontext.loop.stage_staged_validation import stage_staged_validation

        ctx = _make_ctx(strategy={"action": "move", "x": 1})
        events = _make_events()
        result = stage_staged_validation(ctx, events=events, sqlite=_make_sqlite())

        assert result.staged_validation_results is not None
        assert len(result.staged_validation_results) > 0
        # All stages should pass or be skipped
        for sr in result.staged_validation_results:
            assert sr.status in (StageStatus.PASSED, StageStatus.SKIPPED)

    def test_none_strategy_fails_at_syntax(self) -> None:
        """A None candidate should fail at the syntax stage."""
        from autocontext.loop.stage_staged_validation import stage_staged_validation

        ctx = _make_ctx()
        # Simulate a None strategy by setting it directly
        ctx.current_strategy = None  # type: ignore[assignment]
        events = _make_events()
        result = stage_staged_validation(ctx, events=events, sqlite=_make_sqlite())

        assert result.staged_validation_results is not None
        failed = [r for r in result.staged_validation_results if r.status is StageStatus.FAILED]
        assert len(failed) == 1
        assert failed[0].name == "syntax"

    def test_emits_started_and_completed_events(self) -> None:
        """Stage should emit validation_started and validation_completed events."""
        from autocontext.loop.stage_staged_validation import stage_staged_validation

        ctx = _make_ctx()
        events = _make_events()
        stage_staged_validation(ctx, events=events, sqlite=_make_sqlite())

        event_names = [call.args[0] for call in events.emit.call_args_list]
        assert "staged_validation_started" in event_names
        assert "staged_validation_completed" in event_names

    def test_completed_event_includes_stage_results(self) -> None:
        """The completed event payload should include per-stage results."""
        from autocontext.loop.stage_staged_validation import stage_staged_validation

        ctx = _make_ctx()
        events = _make_events()
        stage_staged_validation(ctx, events=events, sqlite=_make_sqlite())

        # Find the completed event
        for call in events.emit.call_args_list:
            if call.args[0] == "staged_validation_completed":
                payload = call.args[1]
                assert "passed" in payload
                assert "stages" in payload
                assert "metrics" in payload
                break
        else:
            raise AssertionError("staged_validation_completed event not found")

    def test_metrics_attached_to_context(self) -> None:
        """After running, metrics dict should be on the context."""
        from autocontext.loop.stage_staged_validation import stage_staged_validation

        ctx = _make_ctx()
        result = stage_staged_validation(ctx, events=_make_events(), sqlite=_make_sqlite())

        assert result.staged_validation_metrics is not None
        assert "total_candidates" in result.staged_validation_metrics
        assert result.staged_validation_metrics["total_candidates"] == 1

    def test_failed_validation_sets_gate_decision_retry(self) -> None:
        """When validation fails, gate_decision should be set to 'retry'."""
        from autocontext.loop.stage_staged_validation import stage_staged_validation

        ctx = _make_ctx()
        ctx.current_strategy = None  # type: ignore[assignment]
        result = stage_staged_validation(ctx, events=_make_events(), sqlite=_make_sqlite())

        assert result.gate_decision == "retry"

    def test_passed_validation_does_not_override_gate_decision(self) -> None:
        """When validation passes, gate_decision should remain empty."""
        from autocontext.loop.stage_staged_validation import stage_staged_validation

        ctx = _make_ctx()
        result = stage_staged_validation(ctx, events=_make_events(), sqlite=_make_sqlite())

        assert result.gate_decision == ""

    def test_code_strategy_with_choose_action_passes(self) -> None:
        """Code strategies should be unwrapped and validated as executable code."""
        from autocontext.loop.stage_staged_validation import stage_staged_validation

        code = "def choose_action(state):\n    return {'action': 'move'}\n"
        ctx = _make_ctx(strategy={"__code__": code})
        result = stage_staged_validation(ctx, events=_make_events(), sqlite=_make_sqlite())

        assert result.staged_validation_results is not None
        assert [sr.name for sr in result.staged_validation_results] == [
            "syntax",
            "contract",
            "deterministic",
            "edge_case",
            "evaluation_ready",
        ]
        assert [sr.status for sr in result.staged_validation_results] == [
            StageStatus.PASSED,
            StageStatus.PASSED,
            StageStatus.PASSED,
            StageStatus.SKIPPED,
            StageStatus.PASSED,
        ]

    def test_code_strategy_missing_choose_action_fails_contract(self) -> None:
        """Wrapped code should fail executable validation when the entry point is missing."""
        from autocontext.loop.stage_staged_validation import stage_staged_validation

        ctx = _make_ctx(strategy={"__code__": "def helper():\n    return {}\n"})
        result = stage_staged_validation(ctx, events=_make_events(), sqlite=_make_sqlite())

        assert result.staged_validation_results is not None
        failed = [r for r in result.staged_validation_results if r.status is StageStatus.FAILED]
        assert len(failed) == 1
        assert failed[0].name == "contract"
        assert failed[0].error_code == "missing_entry_point"
        assert result.gate_decision == "retry"

    def test_skipped_stages_do_not_block(self) -> None:
        """Stages that skip (e.g., no edge fixtures) should not cause failure."""
        from autocontext.loop.stage_staged_validation import stage_staged_validation

        ctx = _make_ctx(scenario=FakeScenario())
        result = stage_staged_validation(ctx, events=_make_events(), sqlite=_make_sqlite())

        skipped = [r for r in result.staged_validation_results if r.status is StageStatus.SKIPPED]
        # At minimum edge_case stage should be skipped (no fixtures on FakeScenario)
        assert len(skipped) >= 1
        # But overall validation should pass
        assert result.gate_decision == ""


# ── Storage persistence tests ───────────────────────────────────────────


class TestStagedValidationPersistence:
    def test_results_persisted_to_sqlite(self) -> None:
        """Stage should call sqlite.insert_staged_validation_results."""
        from autocontext.loop.stage_staged_validation import stage_staged_validation

        ctx = _make_ctx()
        sqlite = _make_sqlite()
        stage_staged_validation(ctx, events=_make_events(), sqlite=sqlite)

        sqlite.insert_staged_validation_results.assert_called_once()
        args = sqlite.insert_staged_validation_results.call_args
        assert args[0][0] == "test-run"  # run_id
        assert args[0][1] == 1  # generation_index

    def test_persistence_failure_does_not_crash_stage(self) -> None:
        """If SQLite write fails, the stage should log and continue."""
        from autocontext.loop.stage_staged_validation import stage_staged_validation

        ctx = _make_ctx()
        sqlite = _make_sqlite()
        sqlite.insert_staged_validation_results.side_effect = Exception("db locked")
        # Should not raise
        result = stage_staged_validation(ctx, events=_make_events(), sqlite=sqlite)
        assert result.staged_validation_results is not None


# ── Pipeline wiring tests ───────────────────────────────────────────────


class TestPipelineWiring:
    def test_generation_pipeline_imports_stage(self) -> None:
        """Verify stage_staged_validation is importable from the loop module."""
        from autocontext.loop.stage_staged_validation import stage_staged_validation
        assert callable(stage_staged_validation)
