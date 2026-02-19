"""Cost tracking types — pricing, records, and summaries."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ModelPricing:
    """Pricing for a specific model."""
    model: str
    input_cost_per_1k: float   # USD per 1,000 input tokens
    output_cost_per_1k: float  # USD per 1,000 output tokens


@dataclass(frozen=True, slots=True)
class CostRecord:
    """Cost for a single API call."""
    model: str
    input_tokens: int
    output_tokens: int
    input_cost: float
    output_cost: float
    total_cost: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "input_cost": self.input_cost,
            "output_cost": self.output_cost,
            "total_cost": self.total_cost,
        }


@dataclass(frozen=True, slots=True)
class CostSummary:
    """Aggregated cost across multiple calls."""
    total_cost: float
    total_input_tokens: int
    total_output_tokens: int
    records_count: int
    cost_by_model: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_records(cls, records: list[CostRecord]) -> CostSummary:
        if not records:
            return cls(total_cost=0.0, total_input_tokens=0, total_output_tokens=0,
                       records_count=0, cost_by_model={})
        by_model: dict[str, float] = {}
        for r in records:
            by_model[r.model] = by_model.get(r.model, 0.0) + r.total_cost
        return cls(
            total_cost=round(sum(r.total_cost for r in records), 6),
            total_input_tokens=sum(r.input_tokens for r in records),
            total_output_tokens=sum(r.output_tokens for r in records),
            records_count=len(records),
            cost_by_model=by_model,
        )
