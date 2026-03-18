"""Tests for AC-322: multi-objective advancement contract for generation gating.

Covers: AdvancementMetrics, MetricCategory, AdvancementRationale,
AdvancementContract, evaluate_advancement.
"""

from __future__ import annotations

# ===========================================================================
# AdvancementMetrics
# ===========================================================================


class TestAdvancementMetrics:
    def test_construction(self) -> None:
        from autocontext.harness.pipeline.advancement import AdvancementMetrics

        m = AdvancementMetrics(
            best_score=0.85,
            mean_score=0.78,
            previous_best=0.70,
            score_variance=0.01,
            sample_count=5,
            error_rate=0.0,
            crash_count=0,
            confidence=0.9,
            sample_agreement=0.95,
            search_proxy_score=0.85,
            resolved_truth_score=None,
            generalization_gap=None,
            cost_usd=0.15,
            tokens_used=30000,
        )
        assert m.best_score == 0.85
        assert m.delta == 0.15  # best_score - previous_best

    def test_delta_computed(self) -> None:
        from autocontext.harness.pipeline.advancement import AdvancementMetrics

        m = AdvancementMetrics(
            best_score=0.72, mean_score=0.68, previous_best=0.70,
            score_variance=0.02, sample_count=3,
        )
        assert abs(m.delta - 0.02) < 0.001

    def test_roundtrip(self) -> None:
        from autocontext.harness.pipeline.advancement import AdvancementMetrics

        m = AdvancementMetrics(
            best_score=0.9, mean_score=0.85, previous_best=0.8,
            score_variance=0.005, sample_count=5,
            confidence=0.95, resolved_truth_score=0.88,
            previous_resolved_truth_score=0.84,
        )
        d = m.to_dict()
        restored = AdvancementMetrics.from_dict(d)
        assert restored.best_score == 0.9
        assert restored.confidence == 0.95
        assert restored.resolved_truth_score == 0.88
        assert restored.previous_resolved_truth_score == 0.84


# ===========================================================================
# AdvancementRationale
# ===========================================================================


class TestAdvancementRationale:
    def test_construction(self) -> None:
        from autocontext.harness.pipeline.advancement import AdvancementRationale

        r = AdvancementRationale(
            decision="advance",
            reason="Score improved with high confidence",
            component_scores={
                "score_delta": 0.9,
                "robustness": 0.8,
                "confidence": 0.95,
            },
            binding_checks=["score_delta"],
            proxy_signals=["confidence"],
            risk_flags=[],
        )
        assert r.decision == "advance"
        assert "score_delta" in r.binding_checks

    def test_roundtrip(self) -> None:
        from autocontext.harness.pipeline.advancement import AdvancementRationale

        r = AdvancementRationale(
            decision="rollback",
            reason="High error rate",
            component_scores={"error_rate": 0.3},
            binding_checks=["error_rate"],
            proxy_signals=[],
            risk_flags=["error_rate above threshold"],
        )
        d = r.to_dict()
        restored = AdvancementRationale.from_dict(d)
        assert restored.decision == "rollback"
        assert "error_rate above threshold" in restored.risk_flags


# ===========================================================================
# evaluate_advancement — advance cases
# ===========================================================================


class TestEvaluateAdvancement:
    def test_advance_on_clear_improvement(self) -> None:
        from autocontext.harness.pipeline.advancement import (
            AdvancementMetrics,
            evaluate_advancement,
        )

        metrics = AdvancementMetrics(
            best_score=0.85, mean_score=0.80, previous_best=0.70,
            score_variance=0.005, sample_count=5,
            confidence=0.9, error_rate=0.0,
        )
        rationale = evaluate_advancement(metrics, min_delta=0.005)
        assert rationale.decision == "advance"

    def test_rollback_on_regression(self) -> None:
        from autocontext.harness.pipeline.advancement import (
            AdvancementMetrics,
            evaluate_advancement,
        )

        metrics = AdvancementMetrics(
            best_score=0.60, mean_score=0.55, previous_best=0.70,
            score_variance=0.01, sample_count=5,
        )
        rationale = evaluate_advancement(metrics, min_delta=0.005)
        assert rationale.decision == "rollback"

    def test_retry_on_marginal_improvement(self) -> None:
        from autocontext.harness.pipeline.advancement import (
            AdvancementMetrics,
            evaluate_advancement,
        )

        metrics = AdvancementMetrics(
            best_score=0.705, mean_score=0.69, previous_best=0.70,
            score_variance=0.02, sample_count=3,
        )
        rationale = evaluate_advancement(metrics, min_delta=0.01)
        assert rationale.decision in ("retry", "rollback")

    def test_rollback_on_high_error_rate(self) -> None:
        from autocontext.harness.pipeline.advancement import (
            AdvancementMetrics,
            evaluate_advancement,
        )

        metrics = AdvancementMetrics(
            best_score=0.85, mean_score=0.80, previous_best=0.70,
            score_variance=0.005, sample_count=5,
            error_rate=0.4,  # 40% errors
        )
        rationale = evaluate_advancement(metrics, min_delta=0.005)
        assert rationale.decision == "rollback"
        assert any("error" in f.lower() for f in rationale.risk_flags)

    def test_risk_flag_on_low_confidence(self) -> None:
        from autocontext.harness.pipeline.advancement import (
            AdvancementMetrics,
            evaluate_advancement,
        )

        metrics = AdvancementMetrics(
            best_score=0.85, mean_score=0.80, previous_best=0.70,
            score_variance=0.05, sample_count=2,
            confidence=0.3,
        )
        rationale = evaluate_advancement(metrics, min_delta=0.005)
        assert any("confidence" in f.lower() for f in rationale.risk_flags)

    def test_truth_score_overrides_proxy(self) -> None:
        """When resolved_truth_score exists, it should bind over search_proxy_score."""
        from autocontext.harness.pipeline.advancement import (
            AdvancementMetrics,
            evaluate_advancement,
        )

        metrics = AdvancementMetrics(
            best_score=0.90, mean_score=0.85, previous_best=0.70,
            score_variance=0.005, sample_count=5,
            search_proxy_score=0.90,
            resolved_truth_score=0.55,  # truth says much worse
            previous_resolved_truth_score=0.70,
        )
        rationale = evaluate_advancement(metrics, min_delta=0.005)
        assert "resolved_truth_score" in rationale.binding_checks
        assert rationale.decision in ("retry", "rollback")

    def test_truth_score_without_prior_truth_baseline_uses_explicit_fallback(self) -> None:
        from autocontext.harness.pipeline.advancement import (
            AdvancementMetrics,
            evaluate_advancement,
        )

        metrics = AdvancementMetrics(
            best_score=0.90, mean_score=0.85, previous_best=0.70,
            score_variance=0.005, sample_count=5,
            search_proxy_score=0.90,
            resolved_truth_score=0.55,
        )
        rationale = evaluate_advancement(metrics, min_delta=0.005)
        assert "resolved_truth_score" in rationale.binding_checks
        assert "resolved truth present without prior truth baseline" in rationale.risk_flags

    def test_rationale_has_component_scores(self) -> None:
        from autocontext.harness.pipeline.advancement import (
            AdvancementMetrics,
            evaluate_advancement,
        )

        metrics = AdvancementMetrics(
            best_score=0.85, mean_score=0.80, previous_best=0.70,
            score_variance=0.005, sample_count=5,
        )
        rationale = evaluate_advancement(metrics, min_delta=0.005)
        assert len(rationale.component_scores) > 0
        assert "score_delta" in rationale.component_scores
