"""Result contracts shared by improvement execution and its consumers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

TerminationReason = Literal[
    "threshold_met",
    "max_rounds",
    "plateau_stall",
    "unchanged_output",
    "consecutive_failures",
]


@dataclass(slots=True)
class RoundResult:
    """Result from a single improvement round."""

    round_number: int
    output: str
    score: float
    reasoning: str
    dimension_scores: dict[str, float] = field(default_factory=dict)
    is_revision: bool = False
    judge_failed: bool = False
    worst_dimension: str | None = None
    worst_dimension_score: float | None = None
    round_duration_ms: int | None = None
    evaluator_epoch: str | None = None
    evaluator_spec: str | None = None
    execution_provenance: dict[str, Any] = field(default_factory=dict)
    fixture_provenance: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class ImprovementResult:
    """Result from the full improvement loop."""

    rounds: list[RoundResult]
    best_output: str
    best_score: float
    best_round: int
    total_rounds: int
    met_threshold: bool
    judge_failures: int = 0
    termination_reason: TerminationReason = "max_rounds"
    dimension_trajectory: dict[str, list[float]] = field(default_factory=dict)
    total_internal_retries: int = 0
    duration_ms: int | None = None
    judge_calls: int = 0
    pareto_frontier: list[dict[str, Any]] = field(default_factory=list)
    actionable_side_info: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    evaluator_epoch: str | None = None

    @property
    def evaluator_spec(self) -> str | None:
        return next((r.evaluator_spec for r in self.rounds if r.round_number == self.best_round), None)

    @property
    def evaluation_provenance(self) -> list[dict[str, Any]]:
        return [
            {
                "round_number": r.round_number,
                "evaluator_epoch": r.evaluator_epoch,
                "execution_provenance": r.execution_provenance,
                "fixture_provenance": r.fixture_provenance,
            }
            for r in self.rounds
            if r.execution_provenance or r.fixture_provenance
        ]

    @property
    def improved(self) -> bool:
        """Whether the final score is higher than the initial score."""
        if len(self.rounds) < 2:
            return False
        valid = [r for r in self.rounds if not r.judge_failed]
        if len(valid) < 2:
            return False
        return valid[-1].score > valid[0].score
