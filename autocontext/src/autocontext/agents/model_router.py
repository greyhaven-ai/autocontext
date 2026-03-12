"""Tiered model routing based on complexity signals.

Inspired by Plankton's pattern-based Haiku/Sonnet/Opus routing that matches
problem complexity to appropriate reasoning capacity.  Supports harness-aware
dynamic demotion (AC-164): when harness coverage is strong, the competitor
can be demoted to a cheaper tier since the harness catches invalid strategies.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autocontext.execution.harness_coverage import HarnessCoverage


@dataclass(frozen=True, slots=True)
class TierConfig:
    """Configuration for tiered model routing."""

    enabled: bool = False
    tier_haiku_model: str = "claude-haiku-4-5-20251001"
    tier_sonnet_model: str = "claude-sonnet-4-5-20250929"
    tier_opus_model: str = "claude-opus-4-6"
    # Competitor escalation thresholds
    competitor_haiku_max_gen: int = 3  # Use haiku for first N gens
    competitor_retry_escalation: int = 1  # Retry count that triggers sonnet
    # Roles that always use a minimum tier
    coach_min_tier: str = "sonnet"
    architect_min_tier: str = "opus"
    analyst_min_tier: str = "haiku"
    translator_min_tier: str = "haiku"
    # Harness-aware dynamic demotion (AC-164)
    harness_aware_tiering_enabled: bool = False
    harness_coverage_demotion_threshold: float = 0.8


class ModelRouter:
    """Selects model tier based on role and complexity signals."""

    def __init__(self, config: TierConfig) -> None:
        self._config = config
        self._tier_map = {
            "haiku": config.tier_haiku_model,
            "sonnet": config.tier_sonnet_model,
            "opus": config.tier_opus_model,
        }
        self._tier_order = ["haiku", "sonnet", "opus"]

    def select(
        self,
        role: str,
        *,
        generation: int,
        retry_count: int,
        is_plateau: bool,
        harness_coverage: HarnessCoverage | None = None,
    ) -> str | None:
        """Return model name for the given role and context, or None if routing disabled.

        Args:
            role: Agent role (competitor, analyst, coach, etc.).
            generation: Current generation number.
            retry_count: Number of retries for this generation.
            is_plateau: Whether score progression has plateaued.
            harness_coverage: Optional harness coverage measurement for demotion.
        """
        if not self._config.enabled:
            return None

        min_tiers = {
            # competitor tier is computed dynamically below
            "analyst": self._config.analyst_min_tier,
            "coach": self._config.coach_min_tier,
            "architect": self._config.architect_min_tier,
            "translator": self._config.translator_min_tier,
            "curator": "opus",
        }
        tier = min_tiers.get(role, "sonnet")

        if role == "competitor":
            if generation <= self._config.competitor_haiku_max_gen:
                tier = "haiku"
            else:
                tier = "sonnet"

            # Harness-aware demotion: strong coverage allows cheaper models
            if (
                self._config.harness_aware_tiering_enabled
                and harness_coverage is not None
                and harness_coverage.coverage_score >= self._config.harness_coverage_demotion_threshold
            ):
                from autocontext.execution.harness_coverage import HarnessCoverageAnalyzer

                recommended = HarnessCoverageAnalyzer().recommend_model_tier(harness_coverage)
                if recommended:
                    tier = recommended

            # Escalation overrides demotion — retries and plateau take priority
            if retry_count >= self._config.competitor_retry_escalation:
                tier = self._max_tier(tier, "sonnet")
            if is_plateau:
                tier = "opus"
        elif role in ("analyst", "coach"):
            if is_plateau:
                tier = self._max_tier(tier, "opus")

        return self._tier_map[tier]

    def _max_tier(self, a: str, b: str) -> str:
        """Return the higher of two tiers."""
        return a if self._tier_order.index(a) >= self._tier_order.index(b) else b

    def _min_tier(self, a: str, b: str) -> str:
        """Return the lower of two tiers."""
        return a if self._tier_order.index(a) <= self._tier_order.index(b) else b
