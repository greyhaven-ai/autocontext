"""Trust types — tier classification, scores, and resource budgets."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


class TrustTier(enum.StrEnum):
    PROBATION = "probation"  # advance_rate < 0.3 or insufficient data
    ESTABLISHED = "established"  # advance_rate 0.3-0.6
    TRUSTED = "trusted"  # advance_rate 0.6-0.8
    EXEMPLARY = "exemplary"  # advance_rate > 0.8


@dataclass(frozen=True, slots=True)
class TrustScore:
    """Computed trust score for a single agent role."""

    role: str
    tier: TrustTier
    raw_score: float  # 0.0-1.0, advance_rate * confidence
    observations: int
    confidence: float  # 0.0-1.0, scales with observations
    last_updated: str

    @staticmethod
    def now() -> str:
        return datetime.now(UTC).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "tier": self.tier.value,
            "raw_score": self.raw_score,
            "observations": self.observations,
            "confidence": self.confidence,
            "last_updated": self.last_updated,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TrustScore:
        return cls(
            role=data["role"],
            tier=TrustTier(data["tier"]),
            raw_score=data["raw_score"],
            observations=data["observations"],
            confidence=data["confidence"],
            last_updated=data["last_updated"],
        )


@dataclass(frozen=True, slots=True)
class TrustBudget:
    """Resource budget derived from a trust tier."""

    tier: TrustTier
    max_retries: int
    token_budget_multiplier: float
    cadence_flexibility: bool
    model_upgrade_allowed: bool

    @staticmethod
    def for_tier(tier: TrustTier) -> TrustBudget:
        """Return default budget for the given trust tier."""
        defaults: dict[TrustTier, TrustBudget] = {
            TrustTier.PROBATION: TrustBudget(
                tier=TrustTier.PROBATION,
                max_retries=1,
                token_budget_multiplier=0.8,
                cadence_flexibility=False,
                model_upgrade_allowed=False,
            ),
            TrustTier.ESTABLISHED: TrustBudget(
                tier=TrustTier.ESTABLISHED,
                max_retries=2,
                token_budget_multiplier=1.0,
                cadence_flexibility=False,
                model_upgrade_allowed=True,
            ),
            TrustTier.TRUSTED: TrustBudget(
                tier=TrustTier.TRUSTED,
                max_retries=3,
                token_budget_multiplier=1.2,
                cadence_flexibility=True,
                model_upgrade_allowed=True,
            ),
            TrustTier.EXEMPLARY: TrustBudget(
                tier=TrustTier.EXEMPLARY,
                max_retries=4,
                token_budget_multiplier=1.5,
                cadence_flexibility=True,
                model_upgrade_allowed=True,
            ),
        }
        return defaults[tier]
