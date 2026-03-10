"""Tests for ActionFilterHarness (MTS-87)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from unittest.mock import MagicMock

from mts.execution.action_filter import ActionFilterHarness
from mts.scenarios.base import Observation, Result, ScenarioInterface

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _MockScenario(ScenarioInterface):
    """Minimal scenario that supports enumerate_legal_actions."""

    name = "mock"

    def describe_rules(self) -> str:
        return "mock"

    def describe_strategy_interface(self) -> str:
        return "mock"

    def describe_evaluation_criteria(self) -> str:
        return "mock"

    def initial_state(self, seed: int | None = None) -> dict[str, Any]:
        return {"seed": seed or 0, "terminal": False}

    def get_observation(self, state: Mapping[str, Any], player_id: str) -> Observation:
        return Observation(narrative="mock", state={})

    def validate_actions(self, state: Mapping[str, Any], player_id: str, actions: Mapping[str, Any]) -> tuple[bool, str]:
        if "action" in actions and actions["action"] in ("move_up", "move_down"):
            return True, "ok"
        return False, "invalid action"

    def step(self, state: Mapping[str, Any], actions: Mapping[str, Any]) -> dict[str, Any]:
        return {**dict(state), "terminal": True}

    def is_terminal(self, state: Mapping[str, Any]) -> bool:
        return bool(state.get("terminal", False))

    def get_result(self, state: Mapping[str, Any]) -> Result:
        return Result(score=0.5, summary="mock")

    def replay_to_narrative(self, replay: list[dict[str, Any]]) -> str:
        return "mock"

    def render_frame(self, state: Mapping[str, Any]) -> dict[str, Any]:
        return {}

    def enumerate_legal_actions(self, state: Mapping[str, Any]) -> list[dict[str, Any]] | None:
        if self.is_terminal(state):
            return []
        return [
            {"action": "move_up", "description": "Move one cell up"},
            {"action": "move_down", "description": "Move one cell down"},
            {"action": "capture_flag", "description": "Capture the opponent flag", "row": 1, "col": 5},
        ]


class _NoEnumerateScenario(_MockScenario):
    """Scenario that does not override enumerate_legal_actions (returns None)."""

    name = "no_enumerate"

    def enumerate_legal_actions(self, state: Mapping[str, Any]) -> list[dict[str, Any]] | None:
        return None


def _harness() -> ActionFilterHarness:
    return ActionFilterHarness(_MockScenario())


# ---------------------------------------------------------------------------
# get_legal_actions
# ---------------------------------------------------------------------------

class TestGetLegalActions:
    def test_returns_scenario_actions(self) -> None:
        h = _harness()
        state = {"terminal": False}
        actions = h.get_legal_actions(state)
        assert actions is not None
        assert len(actions) == 3

    def test_terminal_returns_empty(self) -> None:
        h = _harness()
        actions = h.get_legal_actions({"terminal": True})
        assert actions == []

    def test_none_when_not_supported(self) -> None:
        h = ActionFilterHarness(_NoEnumerateScenario())
        assert h.get_legal_actions({"terminal": False}) is None

    def test_falls_back_to_harness_loader(self) -> None:
        loader = MagicMock()
        v = MagicMock()
        v.enumerate_legal_actions.return_value = [{"action": "from_harness", "description": "harness action"}]
        loader.validators = [v]
        h = ActionFilterHarness(_NoEnumerateScenario(), harness_loader=loader)
        result = h.get_legal_actions({"terminal": False})
        assert result is not None
        assert result[0]["action"] == "from_harness"

    def test_scenario_preferred_over_harness(self) -> None:
        loader = MagicMock()
        v = MagicMock()
        v.enumerate_legal_actions.return_value = [{"action": "harness_action", "description": "x"}]
        loader.validators = [v]
        h = ActionFilterHarness(_MockScenario(), harness_loader=loader)
        result = h.get_legal_actions({"terminal": False})
        assert result is not None
        assert result[0]["action"] == "move_up"  # from scenario, not harness

    def test_harness_exception_returns_none(self) -> None:
        loader = MagicMock()
        v = MagicMock()
        v.enumerate_legal_actions.side_effect = RuntimeError("boom")
        loader.validators = [v]
        h = ActionFilterHarness(_NoEnumerateScenario(), harness_loader=loader)
        assert h.get_legal_actions({"terminal": False}) is None


# ---------------------------------------------------------------------------
# format_action_prompt
# ---------------------------------------------------------------------------

class TestFormatActionPrompt:
    def test_numbered_list(self) -> None:
        h = _harness()
        actions = h.get_legal_actions({"terminal": False})
        assert actions is not None
        prompt = h.format_action_prompt(actions)
        assert "1. move_up" in prompt
        assert "2. move_down" in prompt
        assert "3. capture_flag" in prompt
        assert "Select an action by number:" in prompt

    def test_includes_description(self) -> None:
        h = _harness()
        actions = h.get_legal_actions({"terminal": False})
        assert actions is not None
        prompt = h.format_action_prompt(actions)
        assert "Move one cell up" in prompt

    def test_includes_row_col(self) -> None:
        h = _harness()
        actions = h.get_legal_actions({"terminal": False})
        assert actions is not None
        prompt = h.format_action_prompt(actions)
        assert "row 1" in prompt
        assert "col 5" in prompt

    def test_continuous_type_formatting(self) -> None:
        h = _harness()
        actions = [
            {"action": "weight", "description": "A weight", "type": "continuous", "range": [0.0, 1.0]},
        ]
        prompt = h.format_action_prompt(actions)
        assert "Provide a JSON object" in prompt
        assert '"weight": 0.5' in prompt

    def test_empty_actions(self) -> None:
        h = _harness()
        prompt = h.format_action_prompt([])
        assert prompt == "No actions available."


# ---------------------------------------------------------------------------
# parse_action_selection
# ---------------------------------------------------------------------------

class TestParseActionSelection:
    def test_numeric_index(self) -> None:
        h = _harness()
        actions = h.get_legal_actions({"terminal": False})
        assert actions is not None
        result = h.parse_action_selection("1", actions)
        assert result is not None
        assert result["action"] == "move_up"

    def test_numeric_with_text(self) -> None:
        h = _harness()
        actions = h.get_legal_actions({"terminal": False})
        assert actions is not None
        result = h.parse_action_selection("I choose 2", actions)
        assert result is not None
        assert result["action"] == "move_down"

    def test_numeric_with_whitespace(self) -> None:
        h = _harness()
        actions = h.get_legal_actions({"terminal": False})
        assert actions is not None
        result = h.parse_action_selection("  3  ", actions)
        assert result is not None
        assert result["action"] == "capture_flag"

    def test_out_of_range_index(self) -> None:
        h = _harness()
        actions = h.get_legal_actions({"terminal": False})
        assert actions is not None
        # Index 99 is out of range, but "move_up" is not in "99"
        result = h.parse_action_selection("99", actions)
        assert result is None

    def test_action_name_match(self) -> None:
        h = _harness()
        actions = h.get_legal_actions({"terminal": False})
        assert actions is not None
        result = h.parse_action_selection("I want to move_down please", actions)
        assert result is not None
        assert result["action"] == "move_down"

    def test_no_match_returns_none(self) -> None:
        h = _harness()
        actions = h.get_legal_actions({"terminal": False})
        assert actions is not None
        result = h.parse_action_selection("something completely unrelated", actions)
        assert result is None

    def test_empty_actions_returns_none(self) -> None:
        h = _harness()
        assert h.parse_action_selection("1", []) is None

    def test_empty_response(self) -> None:
        h = _harness()
        actions = h.get_legal_actions({"terminal": False})
        assert actions is not None
        assert h.parse_action_selection("", actions) is None

    def test_continuous_json_parse(self) -> None:
        h = _harness()
        actions = [
            {"action": "aggression", "description": "x", "type": "continuous", "range": [0.0, 1.0]},
            {"action": "defense", "description": "y", "type": "continuous", "range": [0.0, 1.0]},
        ]
        result = h.parse_action_selection('{"aggression": 0.6, "defense": 0.4}', actions)
        assert result == {"aggression": 0.6, "defense": 0.4}

    def test_continuous_json_missing_key_returns_none(self) -> None:
        h = _harness()
        actions = [
            {"action": "aggression", "description": "x", "type": "continuous", "range": [0.0, 1.0]},
            {"action": "defense", "description": "y", "type": "continuous", "range": [0.0, 1.0]},
        ]
        assert h.parse_action_selection('{"aggression": 0.6}', actions) is None

    def test_continuous_json_out_of_range_returns_none(self) -> None:
        h = _harness()
        actions = [
            {"action": "aggression", "description": "x", "type": "continuous", "range": [0.0, 1.0]},
            {"action": "defense", "description": "y", "type": "continuous", "range": [0.0, 1.0]},
        ]
        assert h.parse_action_selection('{"aggression": 1.6, "defense": 0.4}', actions) is None


# ---------------------------------------------------------------------------
# verify_action
# ---------------------------------------------------------------------------

class TestVerifyAction:
    def test_valid_action(self) -> None:
        h = _harness()
        ok, reason = h.verify_action({}, "player", {"action": "move_up"})
        assert ok is True
        assert reason == "ok"

    def test_invalid_action(self) -> None:
        h = _harness()
        ok, reason = h.verify_action({}, "player", {"action": "fly"})
        assert ok is False
        assert "invalid" in reason

    def test_get_verify_feedback_includes_reason(self) -> None:
        h = _harness()
        feedback = h.get_verify_feedback("bad move", {"terminal": False})
        assert "bad move" in feedback
        assert "Please try again." in feedback

    def test_get_verify_feedback_includes_legal_actions(self) -> None:
        h = _harness()
        feedback = h.get_verify_feedback("bad move", {"terminal": False})
        assert "move_up" in feedback
        assert "move_down" in feedback

    def test_get_verify_feedback_no_enumeration(self) -> None:
        h = ActionFilterHarness(_NoEnumerateScenario())
        feedback = h.get_verify_feedback("bad move", {"terminal": False})
        assert "bad move" in feedback
        # No legal actions appended since enumeration returns None
        assert "move_up" not in feedback


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

class TestExport:
    def test_importable_from_package(self) -> None:
        from mts.execution import ActionFilterHarness as AFH
        assert AFH is ActionFilterHarness
