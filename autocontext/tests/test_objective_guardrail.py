"""Tests for AC-325: objective verification as binding guardrail.

Covers: ObjectiveGuardrailPolicy, GuardrailResult, check_objective_guardrail,
ForecastClaim, settle_forecasts.
"""

from __future__ import annotations

# ===========================================================================
# ObjectiveGuardrailPolicy
# ===========================================================================


class TestObjectiveGuardrailPolicy:
    def test_defaults(self) -> None:
        from autocontext.harness.pipeline.objective_guardrail import (
            ObjectiveGuardrailPolicy,
        )

        policy = ObjectiveGuardrailPolicy()
        assert policy.min_recall > 0
        assert policy.max_false_positive_rate < 1.0
        assert policy.enabled is True

    def test_custom(self) -> None:
        from autocontext.harness.pipeline.objective_guardrail import (
            ObjectiveGuardrailPolicy,
        )

        policy = ObjectiveGuardrailPolicy(
            min_recall=0.7,
            min_precision=0.8,
            max_false_positive_rate=0.1,
            max_rubric_objective_gap=0.15,
        )
        assert policy.min_recall == 0.7
        assert policy.max_rubric_objective_gap == 0.15

    def test_roundtrip(self) -> None:
        from autocontext.harness.pipeline.objective_guardrail import (
            ObjectiveGuardrailPolicy,
        )

        policy = ObjectiveGuardrailPolicy(min_recall=0.6)
        d = policy.to_dict()
        restored = ObjectiveGuardrailPolicy.from_dict(d)
        assert restored.min_recall == 0.6


# ===========================================================================
# GuardrailResult
# ===========================================================================


class TestGuardrailResult:
    def test_construction(self) -> None:
        from autocontext.harness.pipeline.objective_guardrail import GuardrailResult

        result = GuardrailResult(
            passed=True,
            reason="All thresholds met",
            violations=[],
            metrics={"recall": 0.8, "precision": 0.9},
        )
        assert result.passed is True

    def test_roundtrip(self) -> None:
        from autocontext.harness.pipeline.objective_guardrail import GuardrailResult

        result = GuardrailResult(
            passed=False,
            reason="Recall too low",
            violations=["recall 0.3 < 0.5"],
            metrics={"recall": 0.3},
        )
        d = result.to_dict()
        restored = GuardrailResult.from_dict(d)
        assert restored.passed is False
        assert len(restored.violations) == 1


# ===========================================================================
# check_objective_guardrail
# ===========================================================================


class TestCheckObjectiveGuardrail:
    def test_passes_when_all_thresholds_met(self) -> None:
        from autocontext.harness.pipeline.objective_guardrail import (
            ObjectiveGuardrailPolicy,
            check_objective_guardrail,
        )

        policy = ObjectiveGuardrailPolicy(
            min_recall=0.5, min_precision=0.5,
            max_false_positive_rate=0.3, max_rubric_objective_gap=0.3,
        )
        result = check_objective_guardrail(
            recall=0.8, precision=0.9,
            false_positive_rate=0.1,
            rubric_score=0.85, objective_recall=0.8,
            policy=policy,
        )
        assert result.passed is True

    def test_fails_on_low_recall(self) -> None:
        from autocontext.harness.pipeline.objective_guardrail import (
            ObjectiveGuardrailPolicy,
            check_objective_guardrail,
        )

        policy = ObjectiveGuardrailPolicy(min_recall=0.7)
        result = check_objective_guardrail(
            recall=0.4, precision=0.9,
            false_positive_rate=0.0,
            rubric_score=0.9, objective_recall=0.4,
            policy=policy,
        )
        assert result.passed is False
        assert any("recall" in v.lower() for v in result.violations)

    def test_fails_on_high_false_positive_rate(self) -> None:
        from autocontext.harness.pipeline.objective_guardrail import (
            ObjectiveGuardrailPolicy,
            check_objective_guardrail,
        )

        policy = ObjectiveGuardrailPolicy(max_false_positive_rate=0.2)
        result = check_objective_guardrail(
            recall=0.8, precision=0.5,
            false_positive_rate=0.5,
            rubric_score=0.8, objective_recall=0.8,
            policy=policy,
        )
        assert result.passed is False

    def test_fails_on_rubric_objective_gap(self) -> None:
        from autocontext.harness.pipeline.objective_guardrail import (
            ObjectiveGuardrailPolicy,
            check_objective_guardrail,
        )

        policy = ObjectiveGuardrailPolicy(max_rubric_objective_gap=0.1)
        result = check_objective_guardrail(
            recall=0.5, precision=0.8,
            false_positive_rate=0.1,
            rubric_score=0.90, objective_recall=0.50,
            policy=policy,
        )
        assert result.passed is False
        assert any("gap" in v.lower() for v in result.violations)

    def test_better_objective_score_does_not_count_as_gap(self) -> None:
        from autocontext.harness.pipeline.objective_guardrail import (
            ObjectiveGuardrailPolicy,
            check_objective_guardrail,
        )

        policy = ObjectiveGuardrailPolicy(max_rubric_objective_gap=0.1)
        result = check_objective_guardrail(
            recall=0.9, precision=0.9,
            false_positive_rate=0.0,
            rubric_score=0.60, objective_recall=0.90,
            policy=policy,
        )
        assert result.passed is True
        assert result.metrics["rubric_objective_gap"] == 0.0

    def test_disabled_policy_auto_passes(self) -> None:
        from autocontext.harness.pipeline.objective_guardrail import (
            ObjectiveGuardrailPolicy,
            check_objective_guardrail,
        )

        policy = ObjectiveGuardrailPolicy(enabled=False)
        result = check_objective_guardrail(
            recall=0.0, precision=0.0,
            false_positive_rate=1.0,
            rubric_score=0.9, objective_recall=0.0,
            policy=policy,
        )
        assert result.passed is True

    def test_multiple_violations(self) -> None:
        from autocontext.harness.pipeline.objective_guardrail import (
            ObjectiveGuardrailPolicy,
            check_objective_guardrail,
        )

        policy = ObjectiveGuardrailPolicy(
            min_recall=0.7, min_precision=0.7,
            max_false_positive_rate=0.2, max_rubric_objective_gap=0.1,
        )
        result = check_objective_guardrail(
            recall=0.3, precision=0.4,
            false_positive_rate=0.5,
            rubric_score=0.9, objective_recall=0.3,
            policy=policy,
        )
        assert result.passed is False
        assert len(result.violations) >= 3


# ===========================================================================
# ForecastClaim + settle_forecasts (proper scoring rule support)
# ===========================================================================


class TestForecastSettlement:
    def test_forecast_claim_construction(self) -> None:
        from autocontext.harness.pipeline.objective_guardrail import ForecastClaim

        claim = ForecastClaim(
            claim_id="c1",
            description="Drug A interacts with Drug B",
            confidence=0.85,
            resolved=True,
            ground_truth=True,
        )
        assert claim.confidence == 0.85
        assert claim.ground_truth is True

    def test_settle_perfect_forecasts(self) -> None:
        from autocontext.harness.pipeline.objective_guardrail import (
            ForecastClaim,
            settle_forecasts,
        )

        claims = [
            ForecastClaim("c1", "True claim", 0.9, resolved=True, ground_truth=True),
            ForecastClaim("c2", "False claim", 0.1, resolved=True, ground_truth=False),
        ]
        result = settle_forecasts(claims)
        assert result["brier_score"] < 0.1  # Good calibration
        assert result["num_resolved"] == 2

    def test_settle_poor_forecasts(self) -> None:
        from autocontext.harness.pipeline.objective_guardrail import (
            ForecastClaim,
            settle_forecasts,
        )

        claims = [
            ForecastClaim("c1", "Confident wrong", 0.9, resolved=True, ground_truth=False),
            ForecastClaim("c2", "Confident wrong", 0.1, resolved=True, ground_truth=True),
        ]
        result = settle_forecasts(claims)
        assert result["brier_score"] > 0.5  # Bad calibration

    def test_settle_skips_unresolved(self) -> None:
        from autocontext.harness.pipeline.objective_guardrail import (
            ForecastClaim,
            settle_forecasts,
        )

        claims = [
            ForecastClaim("c1", "Resolved", 0.8, resolved=True, ground_truth=True),
            ForecastClaim("c2", "Pending", 0.7, resolved=False, ground_truth=None),
        ]
        result = settle_forecasts(claims)
        assert result["num_resolved"] == 1
        assert result["num_pending"] == 1

    def test_settle_empty_claims(self) -> None:
        from autocontext.harness.pipeline.objective_guardrail import settle_forecasts

        result = settle_forecasts([])
        assert result["brier_score"] == 0.0
        assert result["num_resolved"] == 0
