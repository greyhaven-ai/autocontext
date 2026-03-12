"""Tests for TrustPolicy — tier classification, scoring, budgets, and decay."""

from autocontext.harness.meta.types import RoleProfile
from autocontext.harness.trust.policy import TrustPolicy, TrustPolicyConfig
from autocontext.harness.trust.types import TrustBudget, TrustTier


def _profile(
    role: str = "analyst",
    generations_observed: int = 10,
    advance_rate: float = 0.5,
) -> RoleProfile:
    """Build a RoleProfile with sensible defaults for non-trust fields."""
    return RoleProfile(
        role=role,
        generations_observed=generations_observed,
        advance_rate=advance_rate,
        mean_tokens=1000.0,
        mean_latency_ms=500.0,
        mean_cost_per_gen=0.01,
        cost_per_advance=0.015,
        token_efficiency=0.5,
    )


class TestEvaluate:
    def test_low_observations_always_probation(self) -> None:
        """Roles with fewer than min_observations get PROBATION regardless of advance_rate."""
        policy = TrustPolicy()
        # High advance_rate but only 3 observations (< default min_observations=5)
        score = policy.evaluate(_profile(generations_observed=3, advance_rate=0.95))
        assert score.tier == TrustTier.PROBATION
        assert score.observations == 3

    def test_low_advance_rate_probation(self) -> None:
        """Low advance_rate with sufficient observations gives PROBATION."""
        policy = TrustPolicy()
        score = policy.evaluate(_profile(generations_observed=10, advance_rate=0.1))
        assert score.tier == TrustTier.PROBATION

    def test_established_tier_classification(self) -> None:
        """Advance rate in [0.3, 0.6) yields ESTABLISHED tier."""
        policy = TrustPolicy()
        score = policy.evaluate(_profile(generations_observed=10, advance_rate=0.45))
        assert score.tier == TrustTier.ESTABLISHED

    def test_trusted_tier_classification(self) -> None:
        """Advance rate in [0.6, 0.8) yields TRUSTED tier."""
        policy = TrustPolicy()
        score = policy.evaluate(_profile(generations_observed=10, advance_rate=0.7))
        assert score.tier == TrustTier.TRUSTED

    def test_exemplary_tier_classification(self) -> None:
        """Advance rate >= 0.8 yields EXEMPLARY tier."""
        policy = TrustPolicy()
        score = policy.evaluate(_profile(generations_observed=10, advance_rate=0.85))
        assert score.tier == TrustTier.EXEMPLARY

    def test_confidence_scaling(self) -> None:
        """Confidence should scale linearly with observations below saturation."""
        policy = TrustPolicy()  # confidence_saturation=20
        score = policy.evaluate(_profile(generations_observed=10, advance_rate=0.5))
        assert score.confidence == 10 / 20  # 0.5

    def test_confidence_saturates_at_max(self) -> None:
        """Confidence caps at 1.0 when observations >= confidence_saturation."""
        policy = TrustPolicy()  # confidence_saturation=20
        score = policy.evaluate(_profile(generations_observed=30, advance_rate=0.5))
        assert score.confidence == 1.0

    def test_raw_score_formula(self) -> None:
        """Raw score = advance_rate * confidence."""
        policy = TrustPolicy()  # confidence_saturation=20
        score = policy.evaluate(_profile(generations_observed=10, advance_rate=0.6))
        expected_confidence = 10 / 20  # 0.5
        expected_raw = 0.6 * expected_confidence  # 0.3
        assert score.raw_score == expected_raw
        assert score.confidence == expected_confidence


class TestBudgetFor:
    def test_budget_mapping(self) -> None:
        """budget_for delegates to TrustBudget.for_tier for each tier."""
        policy = TrustPolicy()
        for tier in TrustTier:
            profile = _profile(
                generations_observed=20,
                advance_rate={
                    TrustTier.PROBATION: 0.1,
                    TrustTier.ESTABLISHED: 0.45,
                    TrustTier.TRUSTED: 0.7,
                    TrustTier.EXEMPLARY: 0.9,
                }[tier],
            )
            score = policy.evaluate(profile)
            budget = policy.budget_for(score)
            expected = TrustBudget.for_tier(tier)
            assert budget == expected
            assert budget.tier == tier


class TestDecay:
    def test_decay_reduces_raw_score(self) -> None:
        """Decay should reduce the raw score exponentially."""
        policy = TrustPolicy()  # decay_rate=0.05
        score = policy.evaluate(_profile(generations_observed=20, advance_rate=0.7))
        original_raw = score.raw_score

        decayed = policy.decay(score, generations_since_update=5)
        expected_raw = original_raw * (1 - 0.05) ** 5
        assert abs(decayed.raw_score - expected_raw) < 1e-9
        assert decayed.raw_score < original_raw

    def test_decay_can_downgrade_tier(self) -> None:
        """Sufficient decay should lower the tier classification."""
        policy = TrustPolicy()
        # Start at TRUSTED (advance_rate=0.7, confidence=1.0 at 20 observations)
        score = policy.evaluate(_profile(generations_observed=20, advance_rate=0.7))
        assert score.tier == TrustTier.TRUSTED

        # Heavy decay: (1 - 0.05)^50 ~ 0.0769, effective advance_rate ~ 0.7 * 0.0769 ~ 0.054
        decayed = policy.decay(score, generations_since_update=50)
        assert decayed.tier == TrustTier.PROBATION

    def test_custom_thresholds(self) -> None:
        """Custom config thresholds should shift tier boundaries."""
        config = TrustPolicyConfig(
            established_threshold=0.2,
            trusted_threshold=0.4,
            exemplary_threshold=0.6,
            min_observations=3,
        )
        policy = TrustPolicy(config)

        # advance_rate=0.35 would be ESTABLISHED with defaults, but with custom thresholds
        # 0.35 >= 0.2 (established) and < 0.4 (trusted) -> still ESTABLISHED
        score = policy.evaluate(_profile(generations_observed=5, advance_rate=0.35))
        assert score.tier == TrustTier.ESTABLISHED

        # advance_rate=0.5 would be ESTABLISHED with defaults, but TRUSTED with custom
        score = policy.evaluate(_profile(generations_observed=5, advance_rate=0.5))
        assert score.tier == TrustTier.TRUSTED

        # advance_rate=0.65 would be TRUSTED with defaults, but EXEMPLARY with custom
        score = policy.evaluate(_profile(generations_observed=5, advance_rate=0.65))
        assert score.tier == TrustTier.EXEMPLARY
