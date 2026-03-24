"""Regression test for AC-376: investigation scenario single-action coercion.

The LLM returns {"name": "...", "parameters": {...}} instead of the
required {"actions": [...]} wrapper. validate_actions should coerce
single-action dicts into the actions-list form.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from autocontext.scenarios.base import Result
from autocontext.scenarios.simulation import ActionResult, ActionSpec, SimulationInterface


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
        next_state = dict(state)
        next_state["terminal"] = True
        next_state["last_action"] = action.name
        return (
            ActionResult(
                success=True,
                output=f"executed {action.name}",
                state_changes={"last_action": action.name},
            ),
            next_state,
        )

    def is_terminal(self, state: Mapping[str, Any]) -> bool:
        return bool(state.get("terminal"))

    def evaluate_trace(self, trace: Any, final_state: dict[str, Any]) -> Any:
        return None

    def get_rubric(self) -> str:
        return "Evaluate."

    def get_observation(self, state: Mapping[str, Any], player_id: str) -> Any:
        return None

    def step(self, state: Mapping[str, Any], actions: Mapping[str, Any]) -> dict[str, Any]:
        return super().step(state, actions)

    def get_result(self, state: Mapping[str, Any]) -> Any:
        trace = state.get("_simulation_trace", {"records": []})
        records = trace.get("records", []) if isinstance(trace, Mapping) else []
        return Result(
            score=1.0 if records else 0.0,
            winner="challenger" if records else "incumbent",
            summary="test",
            replay=list(records),
            metrics={"actions_taken": float(len(records))},
        )

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

    def test_single_action_dict_executes_in_step(self) -> None:
        """Coerced single-action dicts should execute when stepped."""
        scenario = _make_scenario()
        next_state = scenario.step(
            scenario.initial_state(),
            {"name": "examine_clue", "parameters": {}},
        )
        assert next_state["last_action"] == "examine_clue"
        trace = next_state["_simulation_trace"]
        assert len(trace["records"]) == 1
        assert trace["records"][0]["action"]["name"] == "examine_clue"

    def test_single_action_dict_executes_in_match(self) -> None:
        """The execute_match path should not drop a coerced single action."""
        scenario = _make_scenario()
        result = scenario.execute_match(
            {"name": "examine_clue", "parameters": {}},
            seed=0,
        )
        assert result.metrics["actions_taken"] == 1.0
        assert len(result.replay) == 1
        assert result.replay[0]["action"]["name"] == "examine_clue"

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
