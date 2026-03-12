"""Tiered gate orchestration: validity gate then quality gate.

The TieredGateOrchestrator sequences two independent gates:
  1. Validity gate — binary pass/fail with its own retry budget
  2. Quality gate — score-delta evaluation (BackpressureGate or TrendAwareGate)

Validity failures never consume the quality gate's retry budget.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from autocontext.harness.pipeline.gate import BackpressureGate
    from autocontext.harness.pipeline.trend_gate import TrendAwareGate
    from autocontext.harness.pipeline.validity_gate import ValidityGate


@dataclass(frozen=True, slots=True)
class TieredGateResult:
    """Result of a two-tier evaluation (validity then quality)."""

    tier: Literal["validity", "quality"]
    decision: Literal["advance", "retry", "rollback"]
    validity_passed: bool
    validity_errors: list[str]
    quality_delta: float | None
    quality_threshold: float | None
    retry_budget_remaining: int
    validity_retry_budget_remaining: int


class TieredGateOrchestrator:
    """Sequences validity and quality gates with independent budgets.

    Tier 1 (Validity): Check strategy validity. If invalid, retry/rollback
    from the validity budget. If valid, proceed to Tier 2.

    Tier 2 (Quality): Run existing backpressure gate. Returns advance/retry/
    rollback from the quality budget.
    """

    def __init__(
        self,
        validity_gate: ValidityGate,
        quality_gate: BackpressureGate | TrendAwareGate,
    ) -> None:
        self._validity_gate = validity_gate
        self._quality_gate = quality_gate

    def evaluate(
        self,
        strategy: dict[str, Any],
        current_best: float,
        previous_best: float,
        state: dict[str, Any] | None = None,
        retry_count: int = 0,
        max_retries: int = 0,
        **quality_kwargs: Any,
    ) -> TieredGateResult:
        """Run two-tier evaluation: validity first, then quality.

        Args:
            strategy: The strategy dict to evaluate.
            current_best: Current generation's best score.
            previous_best: Previous generation's best score.
            state: Optional game state for validity check.
            retry_count: Quality gate retry count.
            max_retries: Quality gate max retries.
            **quality_kwargs: Extra kwargs forwarded to the quality gate
                (e.g. ``history``, ``custom_metrics`` for TrendAwareGate).

        Returns:
            TieredGateResult indicating which tier produced the decision.
        """
        # --- Tier 1: Validity ---
        validity_result = self._validity_gate.check(strategy, state=state)

        if not validity_result.passed:
            can_retry = self._validity_gate.consume_retry()
            decision: Literal["retry", "rollback"] = "retry" if can_retry else "rollback"
            remaining_validity_budget = validity_result.retry_budget_remaining
            if can_retry:
                remaining_validity_budget = max(0, remaining_validity_budget - 1)
            return TieredGateResult(
                tier="validity",
                decision=decision,
                validity_passed=False,
                validity_errors=validity_result.errors,
                quality_delta=None,
                quality_threshold=None,
                retry_budget_remaining=max_retries - retry_count,
                validity_retry_budget_remaining=remaining_validity_budget,
            )

        # --- Tier 2: Quality ---
        gate_decision = self._quality_gate.evaluate(
            previous_best=previous_best,
            current_best=current_best,
            retry_count=retry_count,
            max_retries=max_retries,
            **quality_kwargs,
        )

        return TieredGateResult(
            tier="quality",
            decision=gate_decision.decision,  # type: ignore[arg-type]
            validity_passed=True,
            validity_errors=[],
            quality_delta=gate_decision.delta,
            quality_threshold=gate_decision.threshold,
            retry_budget_remaining=max_retries - retry_count,
            validity_retry_budget_remaining=validity_result.retry_budget_remaining,
        )
