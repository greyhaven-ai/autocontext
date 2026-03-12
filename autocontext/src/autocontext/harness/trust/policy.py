"""Trust policy — evaluates trust from measured performance."""

from __future__ import annotations

from dataclasses import dataclass

from autocontext.harness.meta.types import RoleProfile
from autocontext.harness.trust.types import TrustBudget, TrustScore, TrustTier


@dataclass(frozen=True, slots=True)
class TrustPolicyConfig:
    """Configurable thresholds for trust tier classification."""

    established_threshold: float = 0.3
    trusted_threshold: float = 0.6
    exemplary_threshold: float = 0.8
    min_observations: int = 5
    confidence_saturation: int = 20
    decay_rate: float = 0.05


class TrustPolicy:
    """Evaluates trust from measured performance."""

    def __init__(self, config: TrustPolicyConfig | None = None) -> None:
        self.config = config or TrustPolicyConfig()

    def _classify_tier(self, advance_rate: float) -> TrustTier:
        """Classify tier based on advance_rate against configured thresholds."""
        if advance_rate < self.config.established_threshold:
            return TrustTier.PROBATION
        if advance_rate < self.config.trusted_threshold:
            return TrustTier.ESTABLISHED
        if advance_rate < self.config.exemplary_threshold:
            return TrustTier.TRUSTED
        return TrustTier.EXEMPLARY

    def evaluate(self, profile: RoleProfile) -> TrustScore:
        """Evaluate a role profile and produce a trust score.

        - Confidence scales linearly with observations up to confidence_saturation.
        - Raw score = advance_rate * confidence.
        - Roles with fewer than min_observations are always PROBATION.
        - Otherwise tier is classified by advance_rate against thresholds.
        """
        confidence = min(1.0, profile.generations_observed / self.config.confidence_saturation)
        raw_score = profile.advance_rate * confidence

        if profile.generations_observed < self.config.min_observations:
            tier = TrustTier.PROBATION
        else:
            tier = self._classify_tier(profile.advance_rate)

        return TrustScore(
            role=profile.role,
            tier=tier,
            raw_score=raw_score,
            observations=profile.generations_observed,
            confidence=confidence,
            last_updated=TrustScore.now(),
        )

    def budget_for(self, score: TrustScore) -> TrustBudget:
        """Return the resource budget for the given trust score's tier."""
        return TrustBudget.for_tier(score.tier)

    def decay(self, score: TrustScore, generations_since_update: int) -> TrustScore:
        """Apply exponential decay to a trust score and reclassify tier.

        New raw_score = old raw_score * (1 - decay_rate) ^ generations_since_update.
        Tier is reclassified based on the effective advance rate (raw / confidence).
        """
        new_raw = score.raw_score * (1 - self.config.decay_rate) ** generations_since_update
        effective_advance_rate = new_raw / max(score.confidence, 0.01)
        new_tier = self._classify_tier(effective_advance_rate)

        return TrustScore(
            role=score.role,
            tier=new_tier,
            raw_score=new_raw,
            observations=score.observations,
            confidence=score.confidence,
            last_updated=TrustScore.now(),
        )
