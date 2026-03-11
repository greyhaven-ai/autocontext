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
    """One match result from a generation's tournament."""

    run_id: str
    generation_index: int
    seed: int
    score: float
    passed_validation: bool
    validation_errors: str
