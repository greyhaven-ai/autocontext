"""Regression test for AC-376: investigation scenario single-action coercion.

The LLM returns {"name": "...", "parameters": {...}} instead of the
required {"actions": [...]} wrapper. validate_actions should coerce
single-action dicts into the actions-list form.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from autocontext.scenarios.simulation import ActionSpec, SimulationInterface


class _MinimalSimulation(SimulationInterface):
    """Minimal concrete simulation for testing validate_actions coercion."""

    name = "test_investigation"

    def describe_rules(self) -> str:
        return "Investigation test."

    def describe_strategy_interface(self) -> str:
        return '{"actions": [{"name": "...", "parameters": {...}}]}'

    def describe_evaluation_criteria(self) -> str:
        return "Evaluate investigation quality."

    def describe_scenario(self) -> str:
        return "Investigation test."

    def describe_environment(self) -> Any:
        return None

    def initial_state(self, seed: int | None = None) -> dict[str, Any]:
        return {"step": 0, "terminal": False}

    def get_available_actions(self, state: dict[str, Any]) -> list[ActionSpec]:
        return [
            ActionSpec(name="examine_clue", description="Examine a clue", parameters={}),
            ActionSpec(name="interview_suspect", description="Interview a suspect", parameters={}),
        ]

    def execute_action(self, state: dict[str, Any], action: Any) -> tuple[Any, dict[str, Any]]:
        return (None, state)

    def is_terminal(self, state: Mapping[str, Any]) -> bool:
        return bool(state.get("terminal"))

    def evaluate_trace(self, trace: Any, final_state: dict[str, Any]) -> Any:
        return None

    def get_rubric(self) -> str:
        return "Evaluate."

    def get_observation(self, state: Mapping[str, Any], player_id: str) -> Any:
        return None

    def step(self, state: Mapping[str, Any], actions: Mapping[str, Any]) -> dict[str, Any]:
        return dict(state)

    def get_result(self, state: Mapping[str, Any]) -> Any:
        from autocontext.scenarios.base import Result
        return Result(score=0.5, winner=None, summary="test")

    def replay_to_narrative(self, replay: list[dict[str, Any]]) -> str:
        return ""

    def render_frame(self, state: Mapping[str, Any]) -> dict[str, Any]:
        return {}


def _make_scenario() -> _MinimalSimulation:
    return _MinimalSimulation()


class TestSingleActionCoercion:
    """Verify that single-action dicts are coerced into actions-list form."""

    def test_valid_actions_list_still_works(self) -> None:
        """Normal {"actions": [...]} format should still validate."""
        scenario = _make_scenario()
        state = scenario.initial_state()
        valid, reason = scenario.validate_actions(state, "challenger", {
            "actions": [{"name": "examine_clue", "parameters": {}}],
        })
        assert valid is True
        assert reason == "ok"

    def test_single_action_dict_is_coerced(self) -> None:
        """A single action dict {"name": ..., "parameters": ...} should be
        auto-wrapped into {"actions": [...]}, not rejected."""
        scenario = _make_scenario()
        state = scenario.initial_state()
        valid, reason = scenario.validate_actions(state, "challenger", {
            "name": "examine_clue",
            "parameters": {},
        })
        assert valid is True, f"Expected valid=True but got reason: {reason}"

    def test_single_action_dict_with_reasoning(self) -> None:
        """Single action dict with extra reasoning field should coerce."""
        scenario = _make_scenario()
        state = scenario.initial_state()
        valid, reason = scenario.validate_actions(state, "challenger", {
            "name": "interview_suspect",
            "parameters": {},
            "reasoning": "This suspect looks suspicious",
        })
        assert valid is True, f"Expected valid=True but got reason: {reason}"

    def test_invalid_action_name_still_rejected(self) -> None:
        """Coercion should not prevent validation of unknown action names."""
        scenario = _make_scenario()
        state = scenario.initial_state()
        valid, reason = scenario.validate_actions(state, "challenger", {
            "name": "nonexistent_action",
            "parameters": {},
        })
        assert valid is False
        assert "nonexistent_action" in reason

    def test_completely_invalid_strategy_still_rejected(self) -> None:
        """Strategy with no actions key and no name key should be rejected."""
        scenario = _make_scenario()
        state = scenario.initial_state()
        valid, reason = scenario.validate_actions(state, "challenger", {
            "something_else": "not an action",
        })
        assert valid is False
