"""Training data types for strategy-level export."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class TrainingRecord:
    """One strategy-level training example from a generation."""

    run_id: str
    scenario: str
    generation_index: int
    strategy: str
    score: float
    gate_decision: str
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MatchRecord:
    """One match result from a generation's tournament.

    When replay/state history is available from the scenario's execute_match,
    `replay_json` contains the raw replay and `states` contains per-turn
    state snapshots extracted from replay entries that have a "state" key.
    """

    run_id: str
    generation_index: int
    seed: int
    score: float
    passed_validation: bool
    validation_errors: str
    winner: str | None = None
    strategy: str = ""
    replay_json: str = ""
    states: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "generation_index": self.generation_index,
            "seed": self.seed,
            "score": self.score,
            "passed_validation": self.passed_validation,
            "validation_errors": self.validation_errors,
            "winner": self.winner,
            "strategy": self.strategy,
            "replay_json": self.replay_json,
            "states": self.states,
        }
