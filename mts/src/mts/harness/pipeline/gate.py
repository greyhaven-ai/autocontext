"""Domain-agnostic backpressure gate with score-delta evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class GateDecision:
    decision: str
    delta: float
    threshold: float
    reason: str
    metadata: dict[str, float] = field(default_factory=dict)


class BackpressureGate:
    def __init__(self, min_delta: float = 0.005) -> None:
        self.min_delta = min_delta

    def evaluate(self, previous_best: float, current_best: float, retry_count: int, max_retries: int) -> GateDecision:
        delta = round(current_best - previous_best, 6)
        if delta >= self.min_delta:
            return GateDecision(decision="advance", delta=delta, threshold=self.min_delta, reason="score improved")
        if retry_count < max_retries:
            return GateDecision(
                decision="retry",
                delta=delta,
                threshold=self.min_delta,
                reason="insufficient improvement; retry permitted",
            )
        return GateDecision(
            decision="rollback",
            delta=delta,
            threshold=self.min_delta,
            reason="insufficient improvement and retries exhausted",
        )
