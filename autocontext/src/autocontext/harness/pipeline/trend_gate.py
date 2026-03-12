"""Domain-agnostic trend-aware gate with plateau detection."""

from __future__ import annotations

from dataclasses import dataclass

from autocontext.harness.pipeline.gate import BackpressureGate, GateDecision


@dataclass(frozen=True, slots=True)
class ScoreHistory:
    scores: tuple[float, ...]
    gate_decisions: tuple[str, ...]


class TrendAwareGate:
    def __init__(
        self,
        min_delta: float = 0.005,
        plateau_window: int = 3,
        plateau_relaxation_factor: float = 0.5,
        consecutive_rollback_threshold: int = 3,
    ) -> None:
        self._simple = BackpressureGate(min_delta=min_delta)
        self.min_delta = min_delta
        self.plateau_window = plateau_window
        self.plateau_relaxation_factor = plateau_relaxation_factor
        self.consecutive_rollback_threshold = consecutive_rollback_threshold

    def evaluate(
        self,
        previous_best: float,
        current_best: float,
        retry_count: int,
        max_retries: int,
        history: ScoreHistory | None = None,
        custom_metrics: dict[str, float] | None = None,
    ) -> GateDecision:
        effective_delta = self.min_delta

        if history and len(history.scores) > self.plateau_window:
            recent = history.scores[-(self.plateau_window + 1) : -1]
            spread = max(recent) - min(recent)
            if spread < self.min_delta:
                effective_delta = self.min_delta * self.plateau_relaxation_factor

        if history and len(history.gate_decisions) >= self.consecutive_rollback_threshold:
            recent_decisions = history.gate_decisions[-self.consecutive_rollback_threshold :]
            if all(d == "rollback" for d in recent_decisions):
                effective_delta = self.min_delta * self.plateau_relaxation_factor

        delta = round(current_best - previous_best, 6)
        metadata = custom_metrics or {}

        if delta >= effective_delta:
            return GateDecision(
                decision="advance",
                delta=delta,
                threshold=effective_delta,
                reason="score improved",
                metadata=metadata,
            )
        if retry_count < max_retries:
            return GateDecision(
                decision="retry",
                delta=delta,
                threshold=effective_delta,
                reason="insufficient improvement; retry permitted",
                metadata=metadata,
            )
        return GateDecision(
            decision="rollback",
            delta=delta,
            threshold=effective_delta,
            reason="insufficient improvement and retries exhausted",
            metadata=metadata,
        )
