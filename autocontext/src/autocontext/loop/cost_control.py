"""Cost-aware loop control and routing (AC-327).

Makes cost a first-class signal in loop control so the system
can throttle, demote, or adapt before budget waste accumulates.

Key types:
- CostBudget: total and per-generation budget limits
- CostTracker: accumulated cost with per-generation breakdown
- CostPolicy: thresholds for cost-effectiveness evaluation
- evaluate_cost_effectiveness(): marginal improvement per dollar
- should_throttle(): budget pressure check
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class CostBudget:
    """Budget limits for a run. 0.0 = unlimited."""

    total_usd: float = 0.0
    per_generation_usd: float = 0.0


@dataclass(slots=True)
class _GenerationCost:
    generation: int
    cost_usd: float
    tokens: int


class CostTracker:
    """Tracks accumulated cost with per-generation breakdown."""

    def __init__(self) -> None:
        self._records: list[_GenerationCost] = []

    def record(self, generation: int, cost_usd: float, tokens: int) -> None:
        self._records.append(_GenerationCost(generation, cost_usd, tokens))

    @property
    def total_cost_usd(self) -> float:
        return sum(r.cost_usd for r in self._records)

    @property
    def total_tokens(self) -> int:
        return sum(r.tokens for r in self._records)

    @property
    def per_generation(self) -> list[dict[str, Any]]:
        return [
            {"generation": r.generation, "cost_usd": r.cost_usd, "tokens": r.tokens}
            for r in self._records
        ]

    def generation_cost(self, generation: int) -> float:
        return sum(r.cost_usd for r in self._records if r.generation == generation)


@dataclass(slots=True)
class CostPolicy:
    """Thresholds for cost-effectiveness evaluation."""

    max_cost_per_delta_point: float = 10.0
    throttle_above_total: float = 0.0  # 0 = no throttle based on policy


def evaluate_cost_effectiveness(
    cost_usd: float,
    score_delta: float,
    max_cost_per_delta: float = 10.0,
) -> dict[str, Any]:
    """Compute marginal improvement per dollar."""
    if score_delta <= 0:
        return {
            "cost_per_delta_point": float("inf"),
            "efficient": False,
            "cost_usd": cost_usd,
            "score_delta": score_delta,
        }

    cost_per_delta = cost_usd / score_delta
    return {
        "cost_per_delta_point": round(cost_per_delta, 4),
        "efficient": cost_per_delta <= max_cost_per_delta,
        "cost_usd": cost_usd,
        "score_delta": score_delta,
    }


def throttle_state(
    tracker: CostTracker,
    budget: CostBudget,
    *,
    generation: int | None = None,
    policy: CostPolicy | None = None,
) -> dict[str, Any]:
    """Return budget-pressure details for the live loop."""
    latest_generation = generation
    if latest_generation is None and tracker.per_generation:
        latest_generation = max(int(entry["generation"]) for entry in tracker.per_generation)

    total_cost = tracker.total_cost_usd
    generation_cost = (
        tracker.generation_cost(latest_generation)
        if latest_generation is not None
        else 0.0
    )

    reasons: list[str] = []
    if budget.total_usd > 0 and total_cost >= budget.total_usd:
        reasons.append("budget_total")
    if budget.per_generation_usd > 0 and latest_generation is not None and generation_cost >= budget.per_generation_usd:
        reasons.append("budget_generation")
    if policy is not None and policy.throttle_above_total > 0 and total_cost >= policy.throttle_above_total:
        reasons.append("policy_total")

    return {
        "throttle": bool(reasons),
        "reasons": reasons,
        "generation": latest_generation,
        "total_cost_usd": round(total_cost, 6),
        "generation_cost_usd": round(generation_cost, 6),
    }


def should_throttle(
    tracker: CostTracker,
    budget: CostBudget,
    *,
    generation: int | None = None,
    policy: CostPolicy | None = None,
) -> bool:
    """Check if budget pressure requires throttling."""
    return bool(
        throttle_state(
            tracker,
            budget,
            generation=generation,
            policy=policy,
        )["throttle"]
    )
