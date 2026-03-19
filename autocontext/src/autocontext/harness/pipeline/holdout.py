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
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class HoldoutPolicy:
    """Configurable holdout evaluation policy."""

    holdout_seeds: int = 5
    min_holdout_score: float = 0.5
    max_generalization_gap: float = 0.2
    seed_offset: int = 10000
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "holdout_seeds": self.holdout_seeds,
            "min_holdout_score": self.min_holdout_score,
            "max_generalization_gap": self.max_generalization_gap,
            "seed_offset": self.seed_offset,
            "enabled": self.enabled,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HoldoutPolicy:
        return cls(
            holdout_seeds=data.get("holdout_seeds", 5),
            min_holdout_score=data.get("min_holdout_score", 0.5),
            max_generalization_gap=data.get("max_generalization_gap", 0.2),
            seed_offset=data.get("seed_offset", 10000),
            enabled=data.get("enabled", True),
            metadata=data.get("metadata", {}),
        )


@dataclass(slots=True)
class HoldoutResult:
    """Outcome of holdout evaluation."""

    holdout_mean_score: float
    holdout_scores: list[float]
    in_sample_score: float
    generalization_gap: float
    passed: bool
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "holdout_mean_score": self.holdout_mean_score,
            "holdout_scores": self.holdout_scores,
            "in_sample_score": self.in_sample_score,
            "generalization_gap": self.generalization_gap,
            "passed": self.passed,
            "reason": self.reason,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HoldoutResult:
        return cls(
            holdout_mean_score=data.get("holdout_mean_score", 0.0),
            holdout_scores=data.get("holdout_scores", []),
            in_sample_score=data.get("in_sample_score", 0.0),
            generalization_gap=data.get("generalization_gap", 0.0),
            passed=data.get("passed", False),
            reason=data.get("reason", ""),
            metadata=data.get("metadata", {}),
        )


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
