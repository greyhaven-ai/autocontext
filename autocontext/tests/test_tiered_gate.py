"""Tests for TieredGateResult and TieredGateOrchestrator — MTS-159."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from autocontext.harness.pipeline.gate import BackpressureGate, GateDecision
from autocontext.harness.pipeline.tiered_gate import TieredGateOrchestrator, TieredGateResult
from autocontext.harness.pipeline.trend_gate import ScoreHistory, TrendAwareGate
from autocontext.harness.pipeline.validity_gate import ValidityGate, ValidityGateResult

# ── TieredGateResult dataclass ───────────────────────────────────────────────


class TestTieredGateResult:
    def test_validity_tier_result(self) -> None:
        r = TieredGateResult(
            tier="validity",
            decision="retry",
            validity_passed=False,
            validity_errors=["bad move"],
            quality_delta=None,
            quality_threshold=None,
            retry_budget_remaining=2,
            validity_retry_budget_remaining=3,
        )
        assert r.tier == "validity"
        assert r.decision == "retry"
        assert r.validity_passed is False
        assert r.quality_delta is None
        assert r.quality_threshold is None

    def test_quality_tier_result(self) -> None:
        r = TieredGateResult(
            tier="quality",
            decision="advance",
            validity_passed=True,
            validity_errors=[],
            quality_delta=0.05,
            quality_threshold=0.005,
            retry_budget_remaining=3,
            validity_retry_budget_remaining=5,
        )
        assert r.tier == "quality"
        assert r.decision == "advance"
        assert r.validity_passed is True
        assert r.quality_delta == 0.05
        assert r.quality_threshold == 0.005

    def test_frozen_dataclass(self) -> None:
        r = TieredGateResult(
            tier="validity",
            decision="retry",
            validity_passed=False,
            validity_errors=[],
            quality_delta=None,
            quality_threshold=None,
            retry_budget_remaining=0,
            validity_retry_budget_remaining=0,
        )
        with pytest.raises(AttributeError):
            r.tier = "quality"  # type: ignore[misc]


# ── TieredGateOrchestrator — validity tier only ─────────────────────────────


class TestTieredGateValidityTier:
    """When validity fails, orchestrator should return validity-tier decisions."""

    def _make_validity_gate(
        self, *, passed: bool, errors: list[str] | None = None, budget: int = 5,
    ) -> MagicMock:
        gate = MagicMock(spec=ValidityGate)
        gate.check.return_value = ValidityGateResult(
            passed=passed,
            errors=errors or [],
            harness_errors=errors or [],
            scenario_errors=[],
            retry_budget_remaining=budget,
        )
        gate.consume_retry.return_value = budget > 0
        return gate

    def _make_quality_gate(self) -> BackpressureGate:
        return BackpressureGate(min_delta=0.005)

    def test_invalid_strategy_retry_with_budget(self) -> None:
        validity = self._make_validity_gate(passed=False, errors=["bad move"], budget=3)
        quality = self._make_quality_gate()
        orch = TieredGateOrchestrator(validity_gate=validity, quality_gate=quality)

        result = orch.evaluate(
            strategy={"moves": ["bad"]},
            current_best=0.5,
            previous_best=0.4,
        )

        assert result.tier == "validity"
        assert result.decision == "retry"
        assert result.validity_passed is False
        assert result.validity_errors == ["bad move"]
        assert result.quality_delta is None
        assert result.quality_threshold is None
        assert result.validity_retry_budget_remaining == 2
        validity.consume_retry.assert_called_once()

    def test_invalid_strategy_rollback_when_exhausted(self) -> None:
        validity = self._make_validity_gate(passed=False, errors=["bad move"], budget=0)
        validity.consume_retry.return_value = False
        quality = self._make_quality_gate()
        orch = TieredGateOrchestrator(validity_gate=validity, quality_gate=quality)

        result = orch.evaluate(
            strategy={"moves": ["bad"]},
            current_best=0.5,
            previous_best=0.4,
        )

        assert result.tier == "validity"
        assert result.decision == "rollback"
        assert result.validity_passed is False
        assert result.validity_retry_budget_remaining == 0

    def test_invalid_strategy_does_not_call_quality_gate(self) -> None:
        validity = self._make_validity_gate(passed=False, errors=["bad"], budget=3)
        quality = MagicMock(spec=BackpressureGate)
        orch = TieredGateOrchestrator(validity_gate=validity, quality_gate=quality)

        orch.evaluate(
            strategy={"moves": ["bad"]},
            current_best=0.5,
            previous_best=0.4,
        )

        quality.evaluate.assert_not_called()


# ── TieredGateOrchestrator — quality tier ────────────────────────────────────


class TestTieredGateQualityTier:
    """When validity passes, orchestrator should delegate to quality gate."""

    def _make_validity_gate(self, *, budget: int = 5) -> MagicMock:
        gate = MagicMock(spec=ValidityGate)
        gate.check.return_value = ValidityGateResult(
            passed=True,
            errors=[],
            harness_errors=[],
            scenario_errors=[],
            retry_budget_remaining=budget,
        )
        return gate

    def test_quality_advance(self) -> None:
        validity = self._make_validity_gate(budget=5)
        quality = BackpressureGate(min_delta=0.005)
        orch = TieredGateOrchestrator(validity_gate=validity, quality_gate=quality)

        result = orch.evaluate(
            strategy={"moves": ["up"]},
            current_best=0.55,
            previous_best=0.50,
            retry_count=0,
            max_retries=3,
        )

        assert result.tier == "quality"
        assert result.decision == "advance"
        assert result.validity_passed is True
        assert result.quality_delta is not None
        assert result.quality_delta == pytest.approx(0.05)
        assert result.quality_threshold == 0.005

    def test_quality_retry(self) -> None:
        validity = self._make_validity_gate(budget=5)
        quality = BackpressureGate(min_delta=0.005)
        orch = TieredGateOrchestrator(validity_gate=validity, quality_gate=quality)

        result = orch.evaluate(
            strategy={"moves": ["up"]},
            current_best=0.50,
            previous_best=0.50,
            retry_count=0,
            max_retries=3,
        )

        assert result.tier == "quality"
        assert result.decision == "retry"
        assert result.validity_passed is True
        assert result.quality_delta is not None
        assert result.quality_delta == pytest.approx(0.0)

    def test_quality_rollback(self) -> None:
        validity = self._make_validity_gate(budget=5)
        quality = BackpressureGate(min_delta=0.005)
        orch = TieredGateOrchestrator(validity_gate=validity, quality_gate=quality)

        result = orch.evaluate(
            strategy={"moves": ["up"]},
            current_best=0.50,
            previous_best=0.50,
            retry_count=3,
            max_retries=3,
        )

        assert result.tier == "quality"
        assert result.decision == "rollback"
        assert result.validity_passed is True

    def test_quality_gate_gets_correct_args(self) -> None:
        validity = self._make_validity_gate(budget=5)
        quality = MagicMock(spec=BackpressureGate)
        quality.evaluate.return_value = GateDecision(
            decision="advance", delta=0.05, threshold=0.005, reason="score improved",
        )
        orch = TieredGateOrchestrator(validity_gate=validity, quality_gate=quality)

        orch.evaluate(
            strategy={"moves": ["up"]},
            current_best=0.55,
            previous_best=0.50,
            retry_count=1,
            max_retries=3,
        )

        quality.evaluate.assert_called_once_with(
            previous_best=0.50,
            current_best=0.55,
            retry_count=1,
            max_retries=3,
        )


# ── TieredGateOrchestrator with TrendAwareGate ──────────────────────────────


class TestTieredGateWithTrendAwareGate:
    """TieredGateOrchestrator must work with both BackpressureGate and TrendAwareGate."""

    def _make_validity_gate(self, *, budget: int = 5) -> MagicMock:
        gate = MagicMock(spec=ValidityGate)
        gate.check.return_value = ValidityGateResult(
            passed=True,
            errors=[],
            harness_errors=[],
            scenario_errors=[],
            retry_budget_remaining=budget,
        )
        return gate

    def test_trend_gate_advance(self) -> None:
        validity = self._make_validity_gate(budget=5)
        quality = TrendAwareGate(min_delta=0.005)
        orch = TieredGateOrchestrator(validity_gate=validity, quality_gate=quality)

        result = orch.evaluate(
            strategy={"moves": ["up"]},
            current_best=0.55,
            previous_best=0.50,
            retry_count=0,
            max_retries=3,
        )

        assert result.tier == "quality"
        assert result.decision == "advance"
        assert result.validity_passed is True

    def test_trend_gate_with_history(self) -> None:
        validity = self._make_validity_gate(budget=5)
        quality = TrendAwareGate(min_delta=0.005)
        orch = TieredGateOrchestrator(validity_gate=validity, quality_gate=quality)

        history = ScoreHistory(scores=(0.1, 0.2, 0.3, 0.4), gate_decisions=("advance", "advance", "advance"))
        result = orch.evaluate(
            strategy={"moves": ["up"]},
            current_best=0.55,
            previous_best=0.50,
            retry_count=0,
            max_retries=3,
            history=history,
        )

        assert result.tier == "quality"
        assert result.decision == "advance"

    def test_trend_gate_kwargs_forwarded(self) -> None:
        validity = self._make_validity_gate(budget=5)
        quality = MagicMock(spec=TrendAwareGate)
        quality.evaluate.return_value = GateDecision(
            decision="advance", delta=0.05, threshold=0.005, reason="score improved",
        )
        orch = TieredGateOrchestrator(validity_gate=validity, quality_gate=quality)

        history = ScoreHistory(scores=(0.1, 0.2), gate_decisions=("advance",))
        orch.evaluate(
            strategy={"moves": ["up"]},
            current_best=0.55,
            previous_best=0.50,
            retry_count=0,
            max_retries=3,
            history=history,
            custom_metrics={"coverage": 0.8},
        )

        quality.evaluate.assert_called_once_with(
            previous_best=0.50,
            current_best=0.55,
            retry_count=0,
            max_retries=3,
            history=history,
            custom_metrics={"coverage": 0.8},
        )


# ── Budget isolation (key design requirement) ────────────────────────────────


class TestBudgetIsolation:
    """Validity failures must NEVER consume quality retry budget."""

    def _make_validity_gate(
        self, *, passed: bool, errors: list[str] | None = None, budget: int = 5,
    ) -> MagicMock:
        gate = MagicMock(spec=ValidityGate)
        gate.check.return_value = ValidityGateResult(
            passed=passed,
            errors=errors or [],
            harness_errors=errors or [],
            scenario_errors=[],
            retry_budget_remaining=budget,
        )
        gate.consume_retry.return_value = budget > 0
        return gate

    def test_validity_retry_does_not_affect_quality_budget(self) -> None:
        """After 3 validity retries, quality gate should still get full retry budget."""
        validity = self._make_validity_gate(passed=False, errors=["bad"], budget=2)
        quality = MagicMock(spec=BackpressureGate)
        orch = TieredGateOrchestrator(validity_gate=validity, quality_gate=quality)

        # Simulate 3 validity failures
        for _ in range(3):
            orch.evaluate(
                strategy={"moves": ["bad"]},
                current_best=0.5,
                previous_best=0.4,
            )

        # Quality gate was never called
        quality.evaluate.assert_not_called()

    def test_mixed_validity_then_quality(self) -> None:
        """After validity failures, when a valid strategy arrives, quality gate gets called normally."""
        # First: invalid strategy
        validity_fail = MagicMock(spec=ValidityGate)
        validity_fail.check.return_value = ValidityGateResult(
            passed=False, errors=["bad"], harness_errors=["bad"], scenario_errors=[], retry_budget_remaining=4,
        )
        validity_fail.consume_retry.return_value = True

        quality = MagicMock(spec=BackpressureGate)
        quality.evaluate.return_value = GateDecision(
            decision="advance", delta=0.05, threshold=0.005, reason="score improved",
        )

        orch = TieredGateOrchestrator(validity_gate=validity_fail, quality_gate=quality)

        # First call: invalid
        r1 = orch.evaluate(strategy={"bad": True}, current_best=0.5, previous_best=0.4)
        assert r1.tier == "validity"
        quality.evaluate.assert_not_called()

        # Now switch to valid strategy
        validity_fail.check.return_value = ValidityGateResult(
            passed=True, errors=[], harness_errors=[], scenario_errors=[], retry_budget_remaining=4,
        )

        # Second call: valid strategy
        r2 = orch.evaluate(
            strategy={"good": True}, current_best=0.55, previous_best=0.50,
            retry_count=0, max_retries=3,
        )
        assert r2.tier == "quality"
        assert r2.decision == "advance"
        quality.evaluate.assert_called_once()


# ── State forwarding ─────────────────────────────────────────────────────────


class TestStateForwarding:
    def test_state_passed_to_validity_check(self) -> None:
        validity = MagicMock(spec=ValidityGate)
        validity.check.return_value = ValidityGateResult(
            passed=True, errors=[], harness_errors=[], scenario_errors=[], retry_budget_remaining=5,
        )
        quality = MagicMock(spec=BackpressureGate)
        quality.evaluate.return_value = GateDecision(
            decision="advance", delta=0.05, threshold=0.005, reason="score improved",
        )
        orch = TieredGateOrchestrator(validity_gate=validity, quality_gate=quality)

        custom_state = {"grid": [[1, 2]], "turn": 3}
        orch.evaluate(
            strategy={"moves": ["up"]},
            current_best=0.55,
            previous_best=0.50,
            state=custom_state,
            retry_count=0,
            max_retries=3,
        )

        validity.check.assert_called_once_with({"moves": ["up"]}, state=custom_state)


# ── Edge cases ───────────────────────────────────────────────────────────────


class TestTieredGateEdgeCases:
    def test_no_retry_count_defaults_for_quality_gate(self) -> None:
        """When validity passes and no retry_count/max_retries given, quality gate gets defaults."""
        validity = MagicMock(spec=ValidityGate)
        validity.check.return_value = ValidityGateResult(
            passed=True, errors=[], harness_errors=[], scenario_errors=[], retry_budget_remaining=5,
        )
        quality = MagicMock(spec=BackpressureGate)
        quality.evaluate.return_value = GateDecision(
            decision="advance", delta=0.05, threshold=0.005, reason="score improved",
        )
        orch = TieredGateOrchestrator(validity_gate=validity, quality_gate=quality)

        result = orch.evaluate(
            strategy={"moves": ["up"]},
            current_best=0.55,
            previous_best=0.50,
        )

        assert result.tier == "quality"
        quality.evaluate.assert_called_once_with(
            previous_best=0.50,
            current_best=0.55,
            retry_count=0,
            max_retries=0,
        )

    def test_result_includes_both_budgets(self) -> None:
        validity = MagicMock(spec=ValidityGate)
        validity.check.return_value = ValidityGateResult(
            passed=True, errors=[], harness_errors=[], scenario_errors=[], retry_budget_remaining=4,
        )
        quality = BackpressureGate(min_delta=0.005)
        orch = TieredGateOrchestrator(validity_gate=validity, quality_gate=quality)

        result = orch.evaluate(
            strategy={"moves": ["up"]},
            current_best=0.55,
            previous_best=0.50,
            retry_count=1,
            max_retries=3,
        )

        assert result.validity_retry_budget_remaining == 4
        assert result.retry_budget_remaining == 2  # max_retries(3) - retry_count(1) = 2
