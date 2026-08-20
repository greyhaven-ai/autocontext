from __future__ import annotations

from autocontext.scenarios.base import Observation, Result, ScenarioInterface


class PackageScenario(ScenarioInterface):
    name = "package_scenario"

    def describe_rules(self) -> str:
        return "Return the supplied value."

    def describe_strategy_interface(self) -> str:
        return "A JSON object containing value."

    def describe_evaluation_criteria(self) -> str:
        return "Higher is better."

    def initial_state(self, seed: int | None = None) -> dict:
        return {"seed": seed or 0, "terminal": False}

    def get_observation(self, state: dict, player_id: str) -> Observation:
        return Observation(narrative=player_id, state=state)

    def validate_actions(self, state: dict, player_id: str, actions: dict) -> tuple[bool, str]:
        del state, player_id
        return ("value" in actions, "ok" if "value" in actions else "missing value")

    def step(self, state: dict, actions: dict) -> dict:
        return {**state, "terminal": True, "score": float(actions["value"])}

    def is_terminal(self, state: dict) -> bool:
        return bool(state["terminal"])

    def get_result(self, state: dict) -> Result:
        return Result(score=state["score"], summary="package", replay=[])

    def replay_to_narrative(self, replay: list[dict]) -> str:
        return str(replay)

    def render_frame(self, state: dict) -> dict:
        return state
