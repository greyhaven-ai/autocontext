"""Multi-dimensional scoring for game scenario evaluation (AC-338).

Extends game scenarios with per-dimension scoring so the analyst can
produce findings like "positional_control regressed from 0.8 to 0.6
despite overall win" instead of just "strategy won".

Key types:
- ScoringDimension: named dimension with weight
- DimensionalScore: aggregate + per-dimension scores
- detect_dimension_regression(): find dimensions that regressed
- format_dimension_trajectory(): human-readable trajectory table
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ScoringDimension:
    """A named scoring dimension with weight."""

    name: str
    weight: float = 1.0
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "weight": self.weight,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScoringDimension:
        return cls(
            name=data["name"],
            weight=data.get("weight", 1.0),
            description=data.get("description", ""),
        )


@dataclass(slots=True)
class DimensionalScore:
    """Aggregate score plus per-dimension breakdown."""

    aggregate: float
    dimensions: dict[str, float]
    metadata: dict[str, Any] = field(default_factory=dict)

    def weighted_aggregate(self, dimension_specs: Sequence[ScoringDimension]) -> float:
        """Compute weighted aggregate from dimension specs."""
        total_weight = sum(d.weight for d in dimension_specs)
        if total_weight == 0:
            return 0.0
        weighted_sum = sum(
            self.dimensions.get(d.name, 0.0) * d.weight
            for d in dimension_specs
        )
        return round(weighted_sum / total_weight, 6)

    def to_dict(self) -> dict[str, Any]:
        return {
            "aggregate": self.aggregate,
            "dimensions": self.dimensions,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DimensionalScore:
        return cls(
            aggregate=data.get("aggregate", 0.0),
            dimensions=data.get("dimensions", {}),
            metadata=data.get("metadata", {}),
        )


def normalize_dimension_specs(
    raw_specs: Sequence[dict[str, Any]] | None,
) -> list[ScoringDimension]:
    """Convert scenario-provided dimension specs into typed dimensions."""
    if not raw_specs:
        return []
    return [ScoringDimension.from_dict(spec) for spec in raw_specs if isinstance(spec, dict)]


def extract_dimension_scores(
    metrics: dict[str, Any],
    dimension_specs: Sequence[ScoringDimension],
) -> dict[str, float]:
    """Extract typed dimension scores from scenario metrics."""
    scores: dict[str, float] = {}
    for spec in dimension_specs:
        value = metrics.get(spec.name)
        if isinstance(value, (int, float)):
            scores[spec.name] = round(float(value), 6)
    return scores


def detect_dimension_regression(
    previous: dict[str, float],
    current: dict[str, float],
    threshold: float = 0.1,
) -> list[dict[str, Any]]:
    """Find dimensions that regressed more than threshold.

    Only checks dimensions present in both previous and current.
    """
    regressions: list[dict[str, Any]] = []
    for dim in previous:
        if dim not in current:
            continue
        delta = current[dim] - previous[dim]
        if delta < -threshold:
            regressions.append({
                "dimension": dim,
                "previous": previous[dim],
                "current": current[dim],
                "delta": round(delta, 6),
            })
    return regressions


def format_dimension_trajectory(
    history: Sequence[dict[str, float]],
) -> str:
    """Format dimension score history as a human-readable trajectory table."""
    if not history:
        return ""

    all_dims = sorted({dim for entry in history for dim in entry})
    if not all_dims:
        return ""

    header = "Gen | " + " | ".join(f"{d:>12}" for d in all_dims)
    separator = "-" * len(header)
    lines = [header, separator]

    for gen_idx, entry in enumerate(history):
        scores = " | ".join(
            f"{entry.get(d, 0.0):>12.4f}" for d in all_dims
        )
        lines.append(f"{gen_idx + 1:>3} | {scores}")

    return "\n".join(lines)
