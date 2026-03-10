from __future__ import annotations

import random
from collections.abc import Mapping
from typing import Any

from mts.scenarios.base import Observation, Result, ScenarioInterface


class GridCtfScenario(ScenarioInterface):
    name = "grid_ctf"

    def describe_rules(self) -> str:
        return (
            "20x20 capture-the-flag map with fog of war and three unit archetypes "
            "(Scout, Soldier, Commander). Preserve at least one defender near base."
        )

    def describe_strategy_interface(self) -> str:
        return (
            "Return JSON object with keys `aggression`, `defense`, and `path_bias`, "
            "all floats in [0,1]. Constraint: aggression + defense <= 1.4."
        )

    def describe_evaluation_criteria(self) -> str:
        return (
            "Primary objective is capture progress. Secondary objectives are defender "
            "survivability and resource efficiency."
        )

    def initial_state(self, seed: int | None = None) -> dict[str, Any]:
        rng = random.Random(seed)
        return {
            "seed": seed or 0,
            "enemy_spawn_bias": round(rng.uniform(0.25, 0.75), 3),
            "resource_density": round(rng.uniform(0.1, 0.9), 3),
            "terminal": False,
            "turn": 0,
            "timeline": [],
        }

    def get_observation(self, state: Mapping[str, Any], player_id: str) -> Observation:
        return Observation(
            narrative=(
                f"{player_id} sees mirrored lanes, enemy spawn bias "
                f"{state['enemy_spawn_bias']}, and resource density {state['resource_density']}."
            ),
            state={
                "enemy_spawn_bias": state["enemy_spawn_bias"],
                "resource_density": state["resource_density"],
            },
            constraints=[
                "Maintain at least one defender near base.",
                "Avoid aggression spikes above sustainable energy budget.",
            ],
        )

    def validate_actions(
        self,
        state: Mapping[str, Any],
        player_id: str,
        actions: Mapping[str, Any],
    ) -> tuple[bool, str]:
        del state, player_id
        required = ("aggression", "defense", "path_bias")
        parsed: dict[str, float] = {}
        for key in required:
            value = actions.get(key)
            if not isinstance(value, (int, float)):
                return False, f"missing or invalid field: {key}"
            numeric = float(value)
            if numeric < 0 or numeric > 1:
                return False, f"{key} must be in [0,1]"
            parsed[key] = numeric
        if parsed["aggression"] + parsed["defense"] > 1.4:
            return False, "combined aggression + defense must be <= 1.4"
        return True, "ok"

    def step(self, state: Mapping[str, Any], actions: Mapping[str, Any]) -> dict[str, Any]:
        aggression = float(actions["aggression"])
        defense = float(actions["defense"])
        path_bias = float(actions["path_bias"])
        rng = random.Random(int(state["seed"]))
        stochastic = rng.uniform(-0.07, 0.07)
        capture_progress = max(0.0, min(1.0, 0.55 * aggression + 0.45 * path_bias + stochastic))
        defender_survival = max(0.0, min(1.0, 1.0 - aggression * 0.4 + defense * 0.4))
        energy_efficiency = max(0.0, min(1.0, 1.0 - aggression * 0.3 + defense * 0.1))
        score = max(0.0, min(1.0, capture_progress * 0.6 + defender_survival * 0.25 + energy_efficiency * 0.15))
        timeline = list(state["timeline"])
        timeline.append(
            {
                "event": "turn_complete",
                "turn": int(state["turn"]) + 1,
                "capture_progress": round(capture_progress, 4),
                "defender_survival": round(defender_survival, 4),
                "energy_efficiency": round(energy_efficiency, 4),
            }
        )
        return {
            **dict(state),
            "terminal": True,
            "turn": int(state["turn"]) + 1,
            "score": round(score, 4),
            "metrics": {
                "capture_progress": round(capture_progress, 4),
                "defender_survival": round(defender_survival, 4),
                "energy_efficiency": round(energy_efficiency, 4),
            },
            "timeline": timeline,
        }

    def is_terminal(self, state: Mapping[str, Any]) -> bool:
        return bool(state.get("terminal", False))

    def get_result(self, state: Mapping[str, Any]) -> Result:
        replay = list(state.get("timeline", []))
        score = float(state.get("score", 0.0))
        return Result(
            score=score,
            winner="challenger" if score >= 0.55 else "incumbent",
            summary=f"GridCTF score {score:.4f}",
            replay=replay,
            metrics={k: float(v) for k, v in dict(state.get("metrics", {})).items()},
        )

    def replay_to_narrative(self, replay: list[dict[str, Any]]) -> str:
        if not replay:
            return "No replay events were captured."
        event = replay[-1]
        return (
            "Capture phase ended with progress "
            f"{event.get('capture_progress', 0.0):.2f}, defender survival "
            f"{event.get('defender_survival', 0.0):.2f}, and energy efficiency "
            f"{event.get('energy_efficiency', 0.0):.2f}."
        )

    def enumerate_legal_actions(self, state: Mapping[str, Any]) -> list[dict[str, Any]] | None:
        """Enumerate the strategy parameter space for grid_ctf.

        Grid CTF uses continuous float parameters rather than discrete moves.
        Returns parameter descriptors with valid ranges and constraints so that
        the ActionFilterHarness can present or validate them.
        """
        if self.is_terminal(state):
            return []
        return [
            {
                "action": "aggression",
                "description": "Attack intensity; higher values push harder toward the flag",
                "type": "continuous",
                "range": [0.0, 1.0],
            },
            {
                "action": "defense",
                "description": "Defensive allocation; constraint: aggression + defense <= 1.4",
                "type": "continuous",
                "range": [0.0, 1.0],
            },
            {
                "action": "path_bias",
                "description": "Pathfinding preference; influences capture route selection",
                "type": "continuous",
                "range": [0.0, 1.0],
            },
        ]

    def render_frame(self, state: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "scenario": self.name,
            "turn": int(state.get("turn", 0)),
            "score": float(state.get("score", 0.0)),
            "metrics": state.get("metrics", {}),
        }
