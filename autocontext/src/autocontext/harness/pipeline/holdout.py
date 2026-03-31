"""Holdout evaluation before advancing a generation (AC-323).

Verifies promising generations on held-out seeds before allowing
advancement. Candidates can win the main tournament and still be
blocked if holdout performance regresses.

Key types:
- HoldoutPolicy: configurable holdout parameters per scenario
- HoldoutResult: outcome of holdout evaluation with gap metrics
- HoldoutVerifier: runs holdout evaluation with pluggable evaluator
- holdout_check(): pure function for checking holdout scores
"""

from __future__ import annotations

import statistics
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field


class HoldoutPolicy(BaseModel):
    """Configurable holdout evaluation policy."""

    holdout_seeds: int = 5
    min_holdout_score: float = 0.5
    max_generalization_gap: float = 0.2
    seed_offset: int = 10000
    enabled: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HoldoutPolicy:
        return cls.model_validate(data)


class HoldoutResult(BaseModel):
    """Outcome of holdout evaluation."""

    holdout_mean_score: float
    holdout_scores: list[float]
    in_sample_score: float
    generalization_gap: float
    passed: bool
    reason: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HoldoutResult:
        return cls.model_validate(data)


def holdout_check(
    *,
    holdout_scores: list[float],
    in_sample_score: float,
    policy: HoldoutPolicy,
) -> HoldoutResult:
    """Check holdout scores against policy thresholds."""
    if not holdout_scores:
        return HoldoutResult(
            holdout_mean_score=0.0,
            holdout_scores=[],
            in_sample_score=in_sample_score,
            generalization_gap=in_sample_score,
            passed=False,
            reason="No holdout scores available",
        )

    mean_score = statistics.mean(holdout_scores)
    gap = round(max(0.0, in_sample_score - mean_score), 6)

    if mean_score < policy.min_holdout_score:
        return HoldoutResult(
            holdout_mean_score=round(mean_score, 6),
            holdout_scores=holdout_scores,
            in_sample_score=in_sample_score,
            generalization_gap=gap,
            passed=False,
            reason=(
                f"Holdout mean {mean_score:.4f} below threshold "
                f"{policy.min_holdout_score:.4f}"
            ),
        )

    if gap > policy.max_generalization_gap:
        return HoldoutResult(
            holdout_mean_score=round(mean_score, 6),
            holdout_scores=holdout_scores,
            in_sample_score=in_sample_score,
            generalization_gap=gap,
            passed=False,
            reason=(
                f"Generalization gap {gap:.4f} exceeds max "
                f"{policy.max_generalization_gap:.4f}"
            ),
        )

    return HoldoutResult(
        holdout_mean_score=round(mean_score, 6),
        holdout_scores=holdout_scores,
        in_sample_score=in_sample_score,
        generalization_gap=gap,
        passed=True,
        reason=f"Holdout score {mean_score:.4f} >= {policy.min_holdout_score:.4f}, gap {gap:.4f} OK",
    )


# Evaluate function: (strategy, seed) -> score
EvaluateFn = Callable[[dict[str, Any], int], float]


class HoldoutVerifier:
    """Runs holdout evaluation with a pluggable evaluator."""

    def __init__(
        self,
        policy: HoldoutPolicy,
        evaluate_fn: EvaluateFn,
    ) -> None:
        self._policy = policy
        self._evaluate = evaluate_fn

    def verify(
        self,
        strategy: dict[str, Any],
        in_sample_score: float,
    ) -> HoldoutResult:
        if not self._policy.enabled:
            return HoldoutResult(
                holdout_mean_score=in_sample_score,
                holdout_scores=[],
                in_sample_score=in_sample_score,
                generalization_gap=0.0,
                passed=True,
                reason="Holdout evaluation disabled by policy",
            )

        scores: list[float] = []
        for i in range(self._policy.holdout_seeds):
            seed = self._policy.seed_offset + i
            score = self._evaluate(strategy, seed)
            scores.append(score)

        return holdout_check(
            holdout_scores=scores,
            in_sample_score=in_sample_score,
            policy=self._policy,
        )
