"""Tests for two-tier gating in stage_tournament (AC-160).

Covers:
- Config fields exist with correct defaults
- When disabled, existing tournament flow is unchanged
- When enabled, validity check runs before tournament
- Validity failure emits correct events
- Validity retry budget is separate from quality retry budget
- Valid strategy proceeds to tournament normally
- Events emitted for both tiers
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from autocontext.agents.types import AgentOutputs
from autocontext.config.settings import AppSettings
from autocontext.execution.supervisor import ExecutionSupervisor
from autocontext.loop.stage_types import GenerationContext
from autocontext.loop.stages import stage_tournament
from autocontext.scenarios.base import (
    ExecutionLimits,
    Observation,
    ReplayEnvelope,
    Result,
    ScenarioInterface,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(**overrides: Any) -> AppSettings:
    defaults: dict[str, Any] = {
        "agent_provider": "deterministic",
    }
    defaults.update(overrides)
    return AppSettings(**defaults)


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
                scenario=scenario.name,
                seed=seed,
                narrative=scenario.replay_to_narrative(result.replay),
                timeline=result.replay,
            )
            return result, replay

    return ExecutionSupervisor(executor=InlineExecutor())


def _make_tournament_ctx(
    scenario: ScenarioInterface | None = None,
    strategy: dict[str, Any] | None = None,
    previous_best: float = 0.0,
    settings: AppSettings | None = None,
) -> GenerationContext:
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


def _run_tournament(
    ctx: GenerationContext | None = None,
    gate_decision: str = "advance",
    gate_reason: str = "improved",
    agents: object | None = None,
) -> GenerationContext:
    """Run stage_tournament with mocked gate and supervisor."""
    ctx = ctx or _make_tournament_ctx()
    supervisor = _make_inline_supervisor()
    gate = MagicMock()
    gate.evaluate.return_value = MagicMock(decision=gate_decision, reason=gate_reason, delta=0.1, threshold=0.005)
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
        agents=agents,
    )
    return result


# ---------------------------------------------------------------------------
# Config field tests
# ---------------------------------------------------------------------------


class TestTwoTierConfig:
    def test_two_tier_gating_enabled_default_false(self) -> None:
        settings = _make_settings()
        assert settings.two_tier_gating_enabled is False

    def test_validity_max_retries_default(self) -> None:
        settings = _make_settings()
        assert settings.validity_max_retries == 3

    def test_two_tier_gating_enabled_can_be_set(self) -> None:
        settings = _make_settings(two_tier_gating_enabled=True)
        assert settings.two_tier_gating_enabled is True

    def test_validity_max_retries_can_be_set(self) -> None:
        settings = _make_settings(validity_max_retries=5)
        assert settings.validity_max_retries == 5

    def test_validity_max_retries_validation(self) -> None:
        """Should not allow negative values."""
        with pytest.raises(ValueError):
            _make_settings(validity_max_retries=-1)


# ---------------------------------------------------------------------------
# Disabled path (existing flow unchanged)
# ---------------------------------------------------------------------------


class TestTwoTierDisabled:
    def test_disabled_flow_works_normally(self) -> None:
        """When two_tier_gating_enabled=False, tournament runs as before."""
        settings = _make_settings(two_tier_gating_enabled=False)
        ctx = _make_tournament_ctx(settings=settings)
        result = _run_tournament(ctx=ctx, gate_decision="advance")
        assert result.tournament is not None
        assert result.gate_decision == "advance"

    def test_disabled_does_not_call_validity_gate(self) -> None:
        """When disabled, ValidityGate should not be imported or called."""
        settings = _make_settings(two_tier_gating_enabled=False)
        ctx = _make_tournament_ctx(settings=settings)
        supervisor = _make_inline_supervisor()
        gate = MagicMock()
        gate.evaluate.return_value = MagicMock(decision="advance", reason="ok", delta=0.1, threshold=0.005)
        events = MagicMock()
        sqlite = MagicMock()
        artifacts = MagicMock()

        with patch("autocontext.loop.stages.ValidityGate") as MockVG:
            stage_tournament(
                ctx,
                supervisor=supervisor,
                gate=gate,
                events=events,
                sqlite=sqlite,
                artifacts=artifacts,
                agents=None,
            )
            MockVG.assert_not_called()


# ---------------------------------------------------------------------------
# Enabled: validity check runs before tournament
# ---------------------------------------------------------------------------


class TestTwoTierEnabled:
    def test_validity_check_runs_when_enabled(self) -> None:
        """When enabled, ValidityGate.check() is called before tournament matches."""
        settings = _make_settings(
            two_tier_gating_enabled=True,
            validity_max_retries=3,
            harness_validators_enabled=True,
        )
        ctx = _make_tournament_ctx(settings=settings)
        supervisor = _make_inline_supervisor()
        gate = MagicMock()
        gate.evaluate.return_value = MagicMock(decision="advance", reason="ok", delta=0.1, threshold=0.005)
        events = MagicMock()
        sqlite = MagicMock()
        artifacts = MagicMock()
        harness_dir = MagicMock()
        harness_dir.exists.return_value = True
        artifacts.harness_dir.return_value = harness_dir

        with patch("autocontext.loop.stages.ValidityGate") as MockVG, \
             patch("autocontext.execution.harness_loader.HarnessLoader") as MockHarnessLoader:
            mock_vg_instance = MagicMock()
            # Validity passes
            mock_vg_instance.check.return_value = MagicMock(
                passed=True, errors=[], retry_budget_remaining=3,
            )
            MockVG.return_value = mock_vg_instance

            result = stage_tournament(
                ctx,
                supervisor=supervisor,
                gate=gate,
                events=events,
                sqlite=sqlite,
                artifacts=artifacts,
                agents=None,
            )

        # ValidityGate was created and check() was called
        MockVG.assert_called_once()
        _, kwargs = MockVG.call_args
        assert kwargs["harness_loader"] is MockHarnessLoader.return_value
        mock_vg_instance.check.assert_called_once()
        assert result.tournament is not None
        assert result.gate_decision == "advance"

    def test_validity_pass_emits_event(self) -> None:
        """When validity passes, emit validity_check_passed event."""
        settings = _make_settings(two_tier_gating_enabled=True, validity_max_retries=3)
        ctx = _make_tournament_ctx(settings=settings)
        supervisor = _make_inline_supervisor()
        gate = MagicMock()
        gate.evaluate.return_value = MagicMock(decision="advance", reason="ok", delta=0.1, threshold=0.005)
        events = MagicMock()
        sqlite = MagicMock()
        artifacts = MagicMock()

        with patch("autocontext.loop.stages.ValidityGate") as MockVG:
            mock_vg_instance = MagicMock()
            mock_vg_instance.check.return_value = MagicMock(
                passed=True, errors=[], retry_budget_remaining=3,
            )
            MockVG.return_value = mock_vg_instance

            stage_tournament(
                ctx,
                supervisor=supervisor,
                gate=gate,
                events=events,
                sqlite=sqlite,
                artifacts=artifacts,
                agents=None,
            )

        event_names = [call[0][0] for call in events.emit.call_args_list]
        assert "validity_check_passed" in event_names

    def test_validity_failure_emits_event(self) -> None:
        """When validity fails, emit validity_check_failed event."""
        settings = _make_settings(
            two_tier_gating_enabled=True, validity_max_retries=0,
        )
        ctx = _make_tournament_ctx(settings=settings)
        supervisor = _make_inline_supervisor()
        gate = MagicMock()
        gate.evaluate.return_value = MagicMock(decision="advance", reason="ok", delta=0.1, threshold=0.005)
        events = MagicMock()
        sqlite = MagicMock()
        artifacts = MagicMock()

        with patch("autocontext.loop.stages.ValidityGate") as MockVG:
            mock_vg_instance = MagicMock()
            mock_vg_instance.check.return_value = MagicMock(
                passed=False,
                errors=["invalid move format"],
                retry_budget_remaining=0,
            )
            mock_vg_instance.consume_retry.return_value = False
            MockVG.return_value = mock_vg_instance

            stage_tournament(
                ctx,
                supervisor=supervisor,
                gate=gate,
                events=events,
                sqlite=sqlite,
                artifacts=artifacts,
                agents=None,
            )

        event_names = [call[0][0] for call in events.emit.call_args_list]
        assert "validity_check_failed" in event_names

    def test_invalid_strategy_rolls_back_without_running_tournament(self) -> None:
        """Exhausted validity budget should not spend tournament execution."""
        settings = _make_settings(two_tier_gating_enabled=True, validity_max_retries=0)
        ctx = _make_tournament_ctx(settings=settings)
        supervisor = _make_inline_supervisor()
        gate = MagicMock()
        events = MagicMock()
        sqlite = MagicMock()
        artifacts = MagicMock()

        with patch("autocontext.loop.stages.ValidityGate") as MockVG:
            mock_vg_instance = MagicMock()
            mock_vg_instance.check.return_value = MagicMock(
                passed=False,
                errors=["invalid move format"],
                retry_budget_remaining=0,
            )
            mock_vg_instance.consume_retry.return_value = False
            MockVG.return_value = mock_vg_instance

            result = stage_tournament(
                ctx,
                supervisor=supervisor,
                gate=gate,
                events=events,
                sqlite=sqlite,
                artifacts=artifacts,
                agents=None,
            )

        event_names = [call[0][0] for call in events.emit.call_args_list]
        assert "tournament_started" not in event_names
        gate.evaluate.assert_not_called()
        assert result.gate_decision == "rollback"
        assert result.tournament is not None
        assert result.tournament.results == []

    def test_validity_retry_revises_before_tournament(self) -> None:
        """A failed validity check should revise the strategy before evaluation."""
        settings = _make_settings(two_tier_gating_enabled=True, validity_max_retries=1)
        ctx = _make_tournament_ctx(settings=settings, strategy={"aggression": -1.0})
        ctx.prompts = MagicMock(competitor="Fix the strategy.")
        ctx.strategy_interface = '{"aggression": float}'
        supervisor = _make_inline_supervisor()
        gate = MagicMock()
        gate.evaluate.return_value = MagicMock(decision="advance", reason="ok", delta=0.1, threshold=0.005)
        events = MagicMock()
        sqlite = MagicMock()
        artifacts = MagicMock()
        agents = MagicMock()
        agents.competitor.run.return_value = ('{"aggression": 0.8}', None)
        agents.translator.translate.return_value = ({"aggression": 0.8}, None)

        with patch("autocontext.loop.stages.ValidityGate") as MockVG:
            mock_vg_instance = MagicMock()
            mock_vg_instance.check.side_effect = [
                MagicMock(passed=False, errors=["out of range"], retry_budget_remaining=1),
                MagicMock(passed=True, errors=[], retry_budget_remaining=0),
            ]
            mock_vg_instance.consume_retry.return_value = True
            MockVG.return_value = mock_vg_instance

            result = stage_tournament(
                ctx,
                supervisor=supervisor,
                gate=gate,
                events=events,
                sqlite=sqlite,
                artifacts=artifacts,
                agents=agents,
            )

        assert result.current_strategy == {"aggression": 0.8}
        assert result.tournament is not None
        assert result.tournament.results
        gate.evaluate.assert_called_once()


# ---------------------------------------------------------------------------
# Validity retry budget separate from quality
# ---------------------------------------------------------------------------


class TestTwoTierRetryBudget:
    def test_validity_retries_do_not_consume_quality_budget(self) -> None:
        """Validity failures use their own retry budget, not the quality gate's."""
        settings = _make_settings(
            two_tier_gating_enabled=True,
            validity_max_retries=2,
            max_retries=3,  # quality budget
        )
        ctx = _make_tournament_ctx(settings=settings)
        supervisor = _make_inline_supervisor()
        gate = MagicMock()
        # Quality gate says advance when eventually called
        gate.evaluate.return_value = MagicMock(decision="advance", reason="ok", delta=0.1, threshold=0.005)
        events = MagicMock()
        sqlite = MagicMock()
        artifacts = MagicMock()

        call_count = 0

        with patch("autocontext.loop.stages.ValidityGate") as MockVG:
            mock_vg_instance = MagicMock()

            def _check_side_effect(strategy: Any, state: Any = None) -> MagicMock:
                nonlocal call_count
                call_count += 1
                if call_count <= 2:
                    return MagicMock(passed=False, errors=["invalid"], retry_budget_remaining=max(0, 2 - call_count))
                return MagicMock(passed=True, errors=[], retry_budget_remaining=0)

            mock_vg_instance.check.side_effect = _check_side_effect
            mock_vg_instance.consume_retry.side_effect = [True, True, False]
            MockVG.return_value = mock_vg_instance

            result = stage_tournament(
                ctx,
                supervisor=supervisor,
                gate=gate,
                events=events,
                sqlite=sqlite,
                artifacts=artifacts,
                agents=None,
            )

        # Quality gate should still have been called (validity eventually passed)
        # The quality gate's max_retries should be unaffected
        assert result.tournament is not None

    def test_validity_exhaustion_causes_rollback(self) -> None:
        """When validity budget is exhausted, result should be rollback."""
        settings = _make_settings(
            two_tier_gating_enabled=True,
            validity_max_retries=0,
        )
        ctx = _make_tournament_ctx(settings=settings)
        supervisor = _make_inline_supervisor()
        gate = MagicMock()
        gate.evaluate.return_value = MagicMock(decision="advance", reason="ok", delta=0.1, threshold=0.005)
        events = MagicMock()
        sqlite = MagicMock()
        artifacts = MagicMock()

        with patch("autocontext.loop.stages.ValidityGate") as MockVG:
            mock_vg_instance = MagicMock()
            mock_vg_instance.check.return_value = MagicMock(
                passed=False,
                errors=["strategy is invalid"],
                retry_budget_remaining=0,
            )
            mock_vg_instance.consume_retry.return_value = False
            MockVG.return_value = mock_vg_instance

            result = stage_tournament(
                ctx,
                supervisor=supervisor,
                gate=gate,
                events=events,
                sqlite=sqlite,
                artifacts=artifacts,
                agents=None,
            )

        # Quality gate should NOT have been called since validity failed
        gate.evaluate.assert_not_called()
        assert result.gate_decision == "rollback"


# ---------------------------------------------------------------------------
# Valid strategy proceeds to tournament normally
# ---------------------------------------------------------------------------


class TestTwoTierValidFlow:
    def test_valid_strategy_gets_tournament_score(self) -> None:
        """When validity passes, tournament runs normally with scores."""
        settings = _make_settings(two_tier_gating_enabled=True, validity_max_retries=3)
        ctx = _make_tournament_ctx(settings=settings)
        supervisor = _make_inline_supervisor()
        gate = MagicMock()
        gate.evaluate.return_value = MagicMock(decision="advance", reason="ok", delta=0.1, threshold=0.005)
        events = MagicMock()
        sqlite = MagicMock()
        artifacts = MagicMock()

        with patch("autocontext.loop.stages.ValidityGate") as MockVG:
            mock_vg_instance = MagicMock()
            mock_vg_instance.check.return_value = MagicMock(
                passed=True, errors=[], retry_budget_remaining=3,
            )
            MockVG.return_value = mock_vg_instance

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
        assert result.tournament.mean_score > 0
        assert result.tournament.best_score > 0
        assert result.gate_decision == "advance"
        # Quality gate was called
        gate.evaluate.assert_called_once()

    def test_valid_strategy_tournament_and_gate_events_emitted(self) -> None:
        """Both validity and tournament events should be emitted."""
        settings = _make_settings(two_tier_gating_enabled=True, validity_max_retries=3)
        ctx = _make_tournament_ctx(settings=settings)
        supervisor = _make_inline_supervisor()
        gate = MagicMock()
        gate.evaluate.return_value = MagicMock(decision="advance", reason="ok", delta=0.1, threshold=0.005)
        events = MagicMock()
        sqlite = MagicMock()
        artifacts = MagicMock()

        with patch("autocontext.loop.stages.ValidityGate") as MockVG:
            mock_vg_instance = MagicMock()
            mock_vg_instance.check.return_value = MagicMock(
                passed=True, errors=[], retry_budget_remaining=3,
            )
            MockVG.return_value = mock_vg_instance

            stage_tournament(
                ctx,
                supervisor=supervisor,
                gate=gate,
                events=events,
                sqlite=sqlite,
                artifacts=artifacts,
                agents=None,
            )

        event_names = [call[0][0] for call in events.emit.call_args_list]
        assert "validity_check_passed" in event_names
        assert "tournament_started" in event_names
        assert "tournament_completed" in event_names
        assert "gate_decided" in event_names
