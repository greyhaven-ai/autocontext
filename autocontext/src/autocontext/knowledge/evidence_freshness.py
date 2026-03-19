"""Evidence freshness and decay for hints, lessons, and notebook context (AC-326).

Tracks support count, last-validated generation, confidence for context
items. Decays or demotes stale guidance during prompt assembly.

Key types:
- EvidenceFreshness: per-item freshness metadata
- FreshnessPolicy: decay thresholds
- apply_freshness_decay(): partition items into active/stale
- detect_stale_context(): generate operator warnings
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class EvidenceFreshness:
    """Freshness metadata for a hint, lesson, or context item."""

    item_id: str
    support_count: int
    last_validated_gen: int
    confidence: float
    created_at_gen: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def age(self, current_gen: int) -> int:
        return current_gen - self.last_validated_gen

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "support_count": self.support_count,
            "last_validated_gen": self.last_validated_gen,
            "confidence": self.confidence,
            "created_at_gen": self.created_at_gen,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceFreshness:
        return cls(
            item_id=data["item_id"],
            support_count=data.get("support_count", 0),
            last_validated_gen=data.get("last_validated_gen", 0),
            confidence=data.get("confidence", 0.0),
            created_at_gen=data.get("created_at_gen", 0),
            metadata=data.get("metadata", {}),
        )


@dataclass(slots=True)
class FreshnessPolicy:
    """Configurable decay thresholds."""

    max_age_gens: int = 10
    min_confidence: float = 0.4
    min_support: int = 1


def apply_freshness_decay(
    items: list[EvidenceFreshness],
    current_gen: int,
    policy: FreshnessPolicy,
) -> tuple[list[EvidenceFreshness], list[EvidenceFreshness]]:
    """Partition items into active and stale based on freshness policy."""
    active: list[EvidenceFreshness] = []
    stale: list[EvidenceFreshness] = []

    for item in items:
        is_stale = (
            item.age(current_gen) > policy.max_age_gens
            or item.confidence < policy.min_confidence
            or item.support_count < policy.min_support
        )
        if is_stale:
            stale.append(item)
        else:
            active.append(item)

    return active, stale


def detect_stale_context(
    items: list[EvidenceFreshness],
    current_gen: int,
    policy: FreshnessPolicy,
) -> list[str]:
    """Generate operator warnings for stale context items."""
    _, stale = apply_freshness_decay(items, current_gen, policy)
    warnings: list[str] = []
    for item in stale:
        age = item.age(current_gen)
        warnings.append(
            f"{item.item_id}: stale (age={age} gens, confidence={item.confidence:.2f}, "
            f"support={item.support_count})"
        )
    return warnings
