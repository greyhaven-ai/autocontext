from __future__ import annotations

import random
from collections.abc import Mapping
from typing import Any

from mts.scenarios.base import Observation, Result, ScenarioInterface


class OthelloScenario(ScenarioInterface):
    name = "othello"

    def describe_rules(self) -> str:
        return "Standard Othello opening phase on an 8x8 board. Valid actions optimize mobility and corner pressure."

    def describe_strategy_interface(self) -> str:
        return (
            "Return JSON object with `mobility_weight`, `corner_weight`, and `stability_weight` "
            "as floats in [0,1]."
        )

    def describe_evaluation_criteria(self) -> str:
        return "Optimize weighted mobility, corner access, and disk stability."

    def initial_state(self, seed: int | None = None) -> dict[str, Any]:
        rng = random.Random(seed)
        return {
            "seed": seed or 0,
            "legal_move_count": int(rng.randint(8, 14)),
            "stability_index": round(rng.uniform(0.2, 0.8), 3),
            "terminal": False,
            "timeline": [],
        }

    def get_observation(self, state: Mapping[str, Any], player_id: str) -> Observation:
        return Observation(
            narrative=(
                f"{player_id} in early game with {state['legal_move_count']} legal moves and "
                f"stability index {state['stability_index']}."
            ),
            state={
                "legal_move_count": state["legal_move_count"],
                "stability_index": state["stability_index"],
            },
            constraints=[
                "Corner pressure is high value when mobility is not over-constrained.",
                "Avoid sacrificing stability for marginal mobility gains.",
            ],
        )

    def validate_actions(
        self,
        state: Mapping[str, Any],
        player_id: str,
        actions: Mapping[str, Any],
    ) -> tuple[bool, str]:
        del state, player_id
        for key in ("mobility_weight", "corner_weight", "stability_weight"):
            value = actions.get(key)
            if not isinstance(value, (int, float)):
                return False, f"missing or invalid field: {key}"
            numeric = float(value)
            if numeric < 0 or numeric > 1:
                return False, f"{key} must be in [0,1]"
        return True, "ok"

    def step(self, state: Mapping[str, Any], actions: Mapping[str, Any]) -> dict[str, Any]:
        mobility = float(actions["mobility_weight"])
        corner = float(actions["corner_weight"])
        stability = float(actions["stability_weight"])
        rng = random.Random(int(state["seed"]))
        noise = rng.uniform(-0.05, 0.05)
        weighted = (mobility * 0.35) + (corner * 0.4) + (stability * 0.25) + noise
        score = max(0.0, min(1.0, weighted))
        timeline = list(state["timeline"])
        timeline.append(
            {
                "event": "opening_evaluated",
                "mobility": round(mobility, 4),
                "corner": round(corner, 4),
                "stability": round(stability, 4),
            }
        )
        return {
            **dict(state),
            "terminal": True,
            "score": round(score, 4),
            "timeline": timeline,
            "metrics": {
                "mobility": round(mobility, 4),
                "corner_pressure": round(corner, 4),
                "stability": round(stability, 4),
            },
        }

    def is_terminal(self, state: Mapping[str, Any]) -> bool:
        return bool(state.get("terminal", False))

    def get_result(self, state: Mapping[str, Any]) -> Result:
        score = float(state.get("score", 0.0))
        replay = list(state.get("timeline", []))
        return Result(
            score=score,
            winner="challenger" if score >= 0.52 else "incumbent",
            summary=f"Othello opening score {score:.4f}",
            replay=replay,
            metrics={k: float(v) for k, v in dict(state.get("metrics", {})).items()},
        )

    def replay_to_narrative(self, replay: list[dict[str, Any]]) -> str:
        if not replay:
            return "No Othello replay available."
        latest = replay[-1]
        return (
            "Opening policy emphasized mobility "
            f"{latest.get('mobility', 0.0):.2f}, corner pressure "
            f"{latest.get('corner', 0.0):.2f}, and stability "
            f"{latest.get('stability', 0.0):.2f}."
        )

    def enumerate_legal_actions(self, state: Mapping[str, Any]) -> list[dict[str, Any]] | None:
        """Enumerate the strategy parameter space for othello.

        The othello scenario uses continuous weight parameters rather than
        discrete board placements. Returns parameter descriptors with valid
        ranges so the ActionFilterHarness can present or validate them.
        """
        if self.is_terminal(state):
            return []
        return [
            {
                "action": "mobility_weight",
                "description": "Weight for move availability; higher values prioritize keeping options open",
                "type": "continuous",
                "range": [0.0, 1.0],
            },
            {
                "action": "corner_weight",
                "description": "Weight for corner control; corners are permanently stable once captured",
                "type": "continuous",
                "range": [0.0, 1.0],
            },
            {
                "action": "stability_weight",
                "description": "Weight for disc stability; stable discs cannot be flipped",
                "type": "continuous",
                "range": [0.0, 1.0],
            },
        ]

    def render_frame(self, state: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "scenario": self.name,
            "score": float(state.get("score", 0.0)),
            "metrics": state.get("metrics", {}),
        }
