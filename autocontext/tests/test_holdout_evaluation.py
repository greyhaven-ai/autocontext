"""Tests for AC-323: holdout evaluation before advancing a generation.

Covers: HoldoutPolicy, HoldoutResult, HoldoutVerifier, holdout_check.
"""

from __future__ import annotations

# ===========================================================================
# HoldoutPolicy
# ===========================================================================


class TestHoldoutPolicy:
    def test_defaults(self) -> None:
        from autocontext.harness.pipeline.holdout import HoldoutPolicy

        policy = HoldoutPolicy()
        assert policy.holdout_seeds > 0
        assert 0.0 <= policy.min_holdout_score <= 1.0
        assert policy.enabled is True

    def test_custom(self) -> None:
        from autocontext.harness.pipeline.holdout import HoldoutPolicy

        policy = HoldoutPolicy(
            holdout_seeds=10,
            min_holdout_score=0.6,
            max_generalization_gap=0.15,
            enabled=True,
        )
        assert policy.holdout_seeds == 10
        assert policy.max_generalization_gap == 0.15

    def test_disabled(self) -> None:
        from autocontext.harness.pipeline.holdout import HoldoutPolicy

        policy = HoldoutPolicy(enabled=False)
        assert policy.enabled is False

    def test_roundtrip(self) -> None:
        from autocontext.harness.pipeline.holdout import HoldoutPolicy

        policy = HoldoutPolicy(holdout_seeds=7, min_holdout_score=0.5)
        d = policy.to_dict()
        restored = HoldoutPolicy.from_dict(d)
        assert restored.holdout_seeds == 7


# ===========================================================================
# HoldoutResult
# ===========================================================================


class TestHoldoutResult:
    def test_construction(self) -> None:
        from autocontext.harness.pipeline.holdout import HoldoutResult

        result = HoldoutResult(
            holdout_mean_score=0.72,
            holdout_scores=[0.70, 0.71, 0.74, 0.73],
            in_sample_score=0.85,
            generalization_gap=0.13,
            passed=True,
            reason="Holdout score 0.72 >= threshold 0.60",
        )
        assert result.passed is True
        assert result.generalization_gap == 0.13

    def test_roundtrip(self) -> None:
        from autocontext.harness.pipeline.holdout import HoldoutResult

        result = HoldoutResult(
            holdout_mean_score=0.5,
            holdout_scores=[0.4, 0.6],
            in_sample_score=0.8,
            generalization_gap=0.3,
            passed=False,
            reason="Gap too large",
        )
        d = result.to_dict()
        restored = HoldoutResult.from_dict(d)
        assert restored.passed is False
        assert restored.generalization_gap == 0.3


# ===========================================================================
# holdout_check
# ===========================================================================


class TestHoldoutCheck:
    def test_passes_when_holdout_above_threshold(self) -> None:
        from autocontext.harness.pipeline.holdout import HoldoutPolicy, holdout_check

        policy = HoldoutPolicy(min_holdout_score=0.5, max_generalization_gap=0.3)
        scores = [0.70, 0.72, 0.68, 0.74, 0.71]
        result = holdout_check(
            holdout_scores=scores,
            in_sample_score=0.85,
            policy=policy,
        )
        assert result.passed is True
        assert result.holdout_mean_score > 0.5

    def test_fails_when_holdout_below_threshold(self) -> None:
        from autocontext.harness.pipeline.holdout import HoldoutPolicy, holdout_check

        policy = HoldoutPolicy(min_holdout_score=0.7)
        scores = [0.40, 0.45, 0.42]
        result = holdout_check(
            holdout_scores=scores,
            in_sample_score=0.85,
            policy=policy,
        )
        assert result.passed is False

    def test_fails_when_gap_too_large(self) -> None:
        from autocontext.harness.pipeline.holdout import HoldoutPolicy, holdout_check

        policy = HoldoutPolicy(min_holdout_score=0.5, max_generalization_gap=0.1)
        scores = [0.60, 0.62, 0.58]
        result = holdout_check(
            holdout_scores=scores,
            in_sample_score=0.90,
            policy=policy,
        )
        assert result.passed is False
        assert "gap" in result.reason.lower()

    def test_passes_with_zero_gap(self) -> None:
        from autocontext.harness.pipeline.holdout import HoldoutPolicy, holdout_check

        policy = HoldoutPolicy(min_holdout_score=0.5, max_generalization_gap=0.3)
        scores = [0.85, 0.84, 0.86]
        result = holdout_check(
            holdout_scores=scores,
            in_sample_score=0.85,
            policy=policy,
        )
        assert result.passed is True
        assert result.generalization_gap < 0.05

    def test_better_holdout_score_does_not_count_as_regression(self) -> None:
        from autocontext.harness.pipeline.holdout import HoldoutPolicy, holdout_check

        policy = HoldoutPolicy(min_holdout_score=0.5, max_generalization_gap=0.1)
        result = holdout_check(
            holdout_scores=[0.88, 0.90, 0.92],
            in_sample_score=0.60,
            policy=policy,
        )
        assert result.passed is True
        assert result.generalization_gap == 0.0

    def test_empty_scores_fails(self) -> None:
        from autocontext.harness.pipeline.holdout import HoldoutPolicy, holdout_check

        policy = HoldoutPolicy()
        result = holdout_check(holdout_scores=[], in_sample_score=0.85, policy=policy)
        assert result.passed is False


# ===========================================================================
# HoldoutVerifier
# ===========================================================================


class TestHoldoutVerifier:
    def test_verify_with_evaluator(self) -> None:
        from autocontext.harness.pipeline.holdout import (
            HoldoutPolicy,
            HoldoutVerifier,
        )

        call_count = 0

        def mock_evaluator(strategy: dict, seed: int) -> float:
            nonlocal call_count
            call_count += 1
            return 0.75

        policy = HoldoutPolicy(holdout_seeds=3, min_holdout_score=0.6)
        verifier = HoldoutVerifier(policy=policy, evaluate_fn=mock_evaluator)
        result = verifier.verify(strategy={"aggression": 0.8}, in_sample_score=0.85)

        assert result.passed is True
        assert call_count == 3
        assert len(result.holdout_scores) == 3

    def test_verify_disabled_policy_auto_passes(self) -> None:
        from autocontext.harness.pipeline.holdout import (
            HoldoutPolicy,
            HoldoutVerifier,
        )

        def should_not_be_called(strategy: dict, seed: int) -> float:
            raise AssertionError("Should not evaluate when disabled")

        policy = HoldoutPolicy(enabled=False)
        verifier = HoldoutVerifier(policy=policy, evaluate_fn=should_not_be_called)
        result = verifier.verify(strategy={}, in_sample_score=0.8)

        assert result.passed is True
        assert "disabled" in result.reason.lower()

    def test_verify_uses_different_seeds(self) -> None:
        from autocontext.harness.pipeline.holdout import (
            HoldoutPolicy,
            HoldoutVerifier,
        )

        seeds_seen: list[int] = []

        def track_seeds(strategy: dict, seed: int) -> float:
            seeds_seen.append(seed)
            return 0.7

        policy = HoldoutPolicy(holdout_seeds=5, seed_offset=1000)
        verifier = HoldoutVerifier(policy=policy, evaluate_fn=track_seeds)
        verifier.verify(strategy={}, in_sample_score=0.8)

        assert len(seeds_seen) == 5
        assert len(set(seeds_seen)) == 5  # All unique
        assert all(s >= 1000 for s in seeds_seen)
