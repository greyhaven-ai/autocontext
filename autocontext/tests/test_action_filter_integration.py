"""Integration tests for action filter pipeline (MTS-89).

Tests the ActionFilterHarness with real scenarios and HarnessMode settings.
Covers end-to-end flows for filter and verify modes.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from unittest.mock import MagicMock

from autocontext.config.settings import HarnessMode, validate_harness_mode
from autocontext.execution.action_filter import ActionFilterHarness
from autocontext.scenarios.base import Observation, Result, ScenarioInterface
from autocontext.scenarios.grid_ctf.scenario import GridCtfScenario
from autocontext.scenarios.othello import OthelloScenario

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _RetryScenario(ScenarioInterface):
    """Scenario for testing retry logic with controlled validation."""

    name = "retry_test"
    _attempt = 0

    def describe_rules(self) -> str:
        return "test"

    def describe_strategy_interface(self) -> str:
        return "test"

    def describe_evaluation_criteria(self) -> str:
        return "test"

    def initial_state(self, seed: int | None = None) -> dict[str, Any]:
        return {"terminal": False}

    def get_observation(self, state: Mapping[str, Any], player_id: str) -> Observation:
        return Observation(narrative="test", state={})

    def validate_actions(self, state: Mapping[str, Any], player_id: str, actions: Mapping[str, Any]) -> tuple[bool, str]:
        self._attempt += 1
        if self._attempt <= 2:
            return False, f"invalid attempt {self._attempt}"
        return True, "ok"

    def step(self, state: Mapping[str, Any], actions: Mapping[str, Any]) -> dict[str, Any]:
        return {**dict(state), "terminal": True}

    def is_terminal(self, state: Mapping[str, Any]) -> bool:
        return bool(state.get("terminal", False))

    def get_result(self, state: Mapping[str, Any]) -> Result:
        return Result(score=0.5, summary="test")

    def replay_to_narrative(self, replay: list[dict[str, Any]]) -> str:
        return "test"

    def render_frame(self, state: Mapping[str, Any]) -> dict[str, Any]:
        return {}

    def enumerate_legal_actions(self, state: Mapping[str, Any]) -> list[dict[str, Any]] | None:
        if self.is_terminal(state):
            return []
        return [
            {"action": "valid_action", "description": "The only valid action"},
        ]


# ---------------------------------------------------------------------------
# HarnessMode integration
# ---------------------------------------------------------------------------

class TestHarnessModeIntegration:
    def test_mode_none_skips_harness(self) -> None:
        """HARNESS_MODE=none: ActionFilterHarness not needed."""
        from autocontext.config.settings import AppSettings

        settings = AppSettings()
        settings = settings.model_copy(update={"harness_mode": HarnessMode.NONE})
        assert settings.harness_mode == HarnessMode.NONE

    def test_mode_filter_requires_validators(self) -> None:
        """HARNESS_MODE=filter falls back to none without validators_enabled."""
        from autocontext.config.settings import AppSettings

        settings = AppSettings()
        settings = settings.model_copy(update={
            "harness_mode": HarnessMode.FILTER,
            "harness_validators_enabled": False,
        })
        validated = validate_harness_mode(settings)
        assert validated.harness_mode == HarnessMode.NONE

    def test_mode_verify_requires_validators(self) -> None:
        """HARNESS_MODE=verify falls back to none without validators_enabled."""
        from autocontext.config.settings import AppSettings

        settings = AppSettings()
        settings = settings.model_copy(update={
            "harness_mode": HarnessMode.VERIFY,
            "harness_validators_enabled": False,
        })
        validated = validate_harness_mode(settings)
        assert validated.harness_mode == HarnessMode.NONE

    def test_mode_filter_with_validators(self) -> None:
        """HARNESS_MODE=filter works when validators_enabled=true."""
        from autocontext.config.settings import AppSettings

        settings = AppSettings()
        settings = settings.model_copy(update={
            "harness_mode": HarnessMode.FILTER,
            "harness_validators_enabled": True,
        })
        validated = validate_harness_mode(settings)
        assert validated.harness_mode == HarnessMode.FILTER

    def test_mode_policy_enables_code_strategies(self) -> None:
        """HARNESS_MODE=policy auto-enables code_strategies."""
        from autocontext.config.settings import AppSettings

        settings = AppSettings()
        settings = settings.model_copy(update={
            "harness_mode": HarnessMode.POLICY,
            "code_strategies_enabled": False,
        })
        validated = validate_harness_mode(settings)
        assert validated.code_strategies_enabled is True


# ---------------------------------------------------------------------------
# grid_ctf end-to-end
# ---------------------------------------------------------------------------

class TestGridCtfEndToEnd:
    def test_filter_mode_enumerate_and_format(self) -> None:
        """grid_ctf: enumerate → format → parse round-trip."""
        scenario = GridCtfScenario()
        harness = ActionFilterHarness(scenario)
        state = scenario.initial_state(seed=42)

        actions = harness.get_legal_actions(state)
        assert actions is not None
        assert len(actions) == 3

        prompt = harness.format_action_prompt(actions)
        assert "aggression" in prompt
        assert "defense" in prompt
        assert "path_bias" in prompt

        selected = harness.parse_action_selection(
            '{"aggression": 0.6, "defense": 0.4, "path_bias": 0.7}',
            actions,
        )
        assert selected is not None
        assert selected == {"aggression": 0.6, "defense": 0.4, "path_bias": 0.7}

    def test_filter_mode_action_name_parse(self) -> None:
        """grid_ctf: parse JSON in markdown fence."""
        scenario = GridCtfScenario()
        harness = ActionFilterHarness(scenario)
        state = scenario.initial_state(seed=42)
        actions = harness.get_legal_actions(state)
        assert actions is not None

        selected = harness.parse_action_selection(
            '```json\n{"aggression": 0.5, "defense": 0.5, "path_bias": 0.8}\n```',
            actions,
        )
        assert selected is not None
        assert selected["path_bias"] == 0.8

    def test_verify_mode_valid_strategy(self) -> None:
        """grid_ctf: valid strategy passes verify."""
        scenario = GridCtfScenario()
        harness = ActionFilterHarness(scenario)
        ok, reason = harness.verify_action(
            scenario.initial_state(seed=42),
            "challenger",
            {"aggression": 0.5, "defense": 0.5, "path_bias": 0.5},
        )
        assert ok is True

    def test_verify_mode_invalid_strategy(self) -> None:
        """grid_ctf: invalid strategy triggers feedback."""
        scenario = GridCtfScenario()
        harness = ActionFilterHarness(scenario)
        state = scenario.initial_state(seed=42)
        ok, reason = harness.verify_action(state, "challenger", {"aggression": 2.0})
        assert ok is False

        feedback = harness.get_verify_feedback(reason, state)
        assert "aggression" in feedback
        assert "Please try again." in feedback

    def test_terminal_state_no_actions(self) -> None:
        """grid_ctf: terminal state returns empty actions."""
        scenario = GridCtfScenario()
        harness = ActionFilterHarness(scenario)
        state = scenario.initial_state(seed=42)
        terminal = {**state, "terminal": True}
        actions = harness.get_legal_actions(terminal)
        assert actions == []


# ---------------------------------------------------------------------------
# othello end-to-end
# ---------------------------------------------------------------------------

class TestOthelloEndToEnd:
    def test_filter_mode_enumerate_and_format(self) -> None:
        """othello: enumerate → format → parse round-trip."""
        scenario = OthelloScenario()
        harness = ActionFilterHarness(scenario)
        state = scenario.initial_state(seed=42)

        actions = harness.get_legal_actions(state)
        assert actions is not None
        assert len(actions) == 3

        prompt = harness.format_action_prompt(actions)
        assert "mobility_weight" in prompt
        assert "corner_weight" in prompt
        assert "stability_weight" in prompt

        selected = harness.parse_action_selection(
            '{"mobility_weight": 0.3, "corner_weight": 0.8, "stability_weight": 0.6}',
            actions,
        )
        assert selected is not None
        assert selected["corner_weight"] == 0.8

    def test_verify_mode_valid_strategy(self) -> None:
        """othello: valid strategy passes verify."""
        scenario = OthelloScenario()
        harness = ActionFilterHarness(scenario)
        ok, _ = harness.verify_action(
            scenario.initial_state(seed=42),
            "challenger",
            {"mobility_weight": 0.5, "corner_weight": 0.5, "stability_weight": 0.5},
        )
        assert ok is True


# ---------------------------------------------------------------------------
# Retry / feedback loop
# ---------------------------------------------------------------------------

class TestRetryLogic:
    def test_verify_retry_produces_feedback(self) -> None:
        """Verify mode: invalid action → feedback includes legal actions."""
        scenario = _RetryScenario()
        harness = ActionFilterHarness(scenario)
        state = scenario.initial_state()

        ok, reason = harness.verify_action(state, "player", {"action": "bad"})
        assert ok is False
        feedback = harness.get_verify_feedback(reason, state)
        assert "valid_action" in feedback
        assert "Please try again." in feedback

    def test_verify_eventually_passes(self) -> None:
        """Verify mode: third attempt passes after two rejections."""
        scenario = _RetryScenario()
        harness = ActionFilterHarness(scenario)
        state = scenario.initial_state()

        # Attempt 1 & 2: rejected
        ok1, _ = harness.verify_action(state, "player", {"action": "bad"})
        assert ok1 is False
        ok2, _ = harness.verify_action(state, "player", {"action": "bad"})
        assert ok2 is False
        # Attempt 3: accepted
        ok3, reason3 = harness.verify_action(state, "player", {"action": "good"})
        assert ok3 is True
        assert reason3 == "ok"

    def test_parse_invalid_then_valid(self) -> None:
        """Filter mode: invalid parse returns None, valid parse succeeds."""
        scenario = _RetryScenario()
        harness = ActionFilterHarness(scenario)
        actions = harness.get_legal_actions(scenario.initial_state())
        assert actions is not None

        # Invalid parse
        result1 = harness.parse_action_selection("99", actions)
        assert result1 is None

        # Valid parse
        result2 = harness.parse_action_selection("1", actions)
        assert result2 is not None
        assert result2["action"] == "valid_action"


# ---------------------------------------------------------------------------
# Harness loader fallback
# ---------------------------------------------------------------------------

class TestHarnessLoaderFallback:
    def test_loader_with_enumerate(self) -> None:
        """Harness loader provides actions when scenario doesn't."""
        loader = MagicMock()
        v = MagicMock()
        v.enumerate_legal_actions.return_value = [
            {"action": "loader_move", "description": "From harness loader"},
        ]
        loader.validators = [v]

        scenario = MagicMock(spec=ScenarioInterface)
        scenario.enumerate_legal_actions.return_value = None
        harness = ActionFilterHarness(scenario, harness_loader=loader)

        actions = harness.get_legal_actions({})
        assert actions is not None
        assert actions[0]["action"] == "loader_move"

    def test_no_loader_returns_none(self) -> None:
        """No harness loader and no scenario support → None."""
        scenario = MagicMock(spec=ScenarioInterface)
        scenario.enumerate_legal_actions.return_value = None
        harness = ActionFilterHarness(scenario)

        assert harness.get_legal_actions({}) is None
