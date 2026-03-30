"""Adaptation types — status, result, and policy for config application."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class AdaptationStatus(StrEnum):
    APPLIED = "applied"
    SKIPPED_LOW_CONFIDENCE = "skipped_low_confidence"
    SKIPPED_MAX_CHANGES = "skipped_max_changes"
    SKIPPED_DISABLED = "skipped_disabled"
    DRY_RUN = "dry_run"


@dataclass(frozen=True, slots=True)
class AdaptationResult:
    """Outcome of a single adaptation attempt."""

    timestamp: str
    role: str
    parameter: str  # "model", "cadence"
    previous_value: str
    new_value: str
    confidence: float
    rationale: str
    status: AdaptationStatus

    @staticmethod
    def now() -> str:
        return datetime.now(UTC).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "role": self.role,
            "parameter": self.parameter,
            "previous_value": self.previous_value,
            "new_value": self.new_value,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "status": self.status.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AdaptationResult:
        return cls(
            timestamp=data["timestamp"],
            role=data["role"],
            parameter=data["parameter"],
            previous_value=data["previous_value"],
            new_value=data["new_value"],
            confidence=data["confidence"],
            rationale=data["rationale"],
            status=AdaptationStatus(data["status"]),
        )


@dataclass(frozen=True, slots=True)
class AdaptationPolicy:
    """Controls whether and how adaptations are applied."""

    enabled: bool = False
    min_confidence: float = 0.6
    max_changes_per_cycle: int = 2
    dry_run: bool = False
    allowed_parameters: frozenset[str] = frozenset({"model", "cadence"})
