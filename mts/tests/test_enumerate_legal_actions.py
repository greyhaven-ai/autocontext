"""Tests for ScenarioInterface.enumerate_legal_actions (MTS-84)."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mts.scenarios.base import Observation, Result, ScenarioInterface

# ---------------------------------------------------------------------------
# Minimal concrete scenario for testing
# ---------------------------------------------------------------------------


class _MinimalScenario(ScenarioInterface):
    """Minimal concrete subclass that does NOT override enumerate_legal_actions."""

    name = "minimal"

    def describe_rules(self) -> str:
        return "minimal rules"

    def describe_strategy_interface(self) -> str:
        return '{"action": "str"}'

    def describe_evaluation_criteria(self) -> str:
        return "maximize score"

    def initial_state(self, seed: int | None = None) -> dict[str, Any]:
        return {"turn": 0, "seed": seed}

    def get_observation(self, state: Mapping[str, Any], player_id: str) -> Observation:
        return Observation(narrative="obs", state=dict(state))

    def validate_actions(self, state: Mapping[str, Any], player_id: str, actions: Mapping[str, Any]) -> tuple[bool, str]:
        return True, ""

    def step(self, state: Mapping[str, Any], actions: Mapping[str, Any]) -> dict[str, Any]:
        return {**dict(state), "terminal": True}

    def is_terminal(self, state: Mapping[str, Any]) -> bool:
        return state.get("terminal", False) is True

    def get_result(self, state: Mapping[str, Any]) -> Result:
        return Result(score=0.5, summary="done")

    def replay_to_narrative(self, replay: list[dict[str, Any]]) -> str:
        return "replay"

    def render_frame(self, state: Mapping[str, Any]) -> dict[str, Any]:
        return dict(state)


class _EnumeratingScenario(_MinimalScenario):
    """Subclass that overrides enumerate_legal_actions."""

    name = "enumerating"

    def enumerate_legal_actions(self, state: Mapping[str, Any]) -> list[dict[str, Any]] | None:
        return [
            {"action": "move_up", "description": "Move one cell up"},
            {"action": "move_down", "description": "Move one cell down"},
        ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEnumerateLegalActions:
    def test_default_returns_none(self) -> None:
        """Default implementation returns None (enumeration not supported)."""
        scenario = _MinimalScenario()
        assert scenario.enumerate_legal_actions({"turn": 0}) is None

    def test_override_returns_actions(self) -> None:
        """Subclass can override to return a list of legal actions."""
        scenario = _EnumeratingScenario()
        actions = scenario.enumerate_legal_actions({"turn": 0})
        assert actions is not None
        assert len(actions) == 2
        assert actions[0]["action"] == "move_up"
        assert actions[1]["action"] == "move_down"

    def test_none_vs_empty_list(self) -> None:
        """None means 'not supported', empty list means 'no legal moves'."""
        scenario = _MinimalScenario()
        # Default: not supported
        assert scenario.enumerate_legal_actions({}) is None

        # An override could return empty list (no moves available)
        class _NoMovesScenario(_MinimalScenario):
            def enumerate_legal_actions(self, state: Mapping[str, Any]) -> list[dict[str, Any]] | None:
                return []

        no_moves = _NoMovesScenario()
        result = no_moves.enumerate_legal_actions({})
        assert result is not None
        assert result == []

    def test_existing_scenarios_unaffected(self) -> None:
        """Built-in scenarios that don't override still work normally."""
        from mts.scenarios.grid_ctf.scenario import GridCtfScenario

        scenario = GridCtfScenario()
        assert hasattr(scenario, "enumerate_legal_actions")
        assert scenario.enumerate_legal_actions(scenario.initial_state(seed=42)) is None
