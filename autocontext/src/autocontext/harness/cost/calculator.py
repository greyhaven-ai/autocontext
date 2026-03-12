"""Cost calculator — converts token usage into dollar amounts."""
from __future__ import annotations

from autocontext.harness.core.types import RoleUsage
from autocontext.harness.cost.types import CostRecord, ModelPricing

# Default pricing (Anthropic models, approximate as of 2025)
DEFAULT_PRICING: list[ModelPricing] = [
    ModelPricing("claude-opus-4-6", 0.015, 0.075),
    ModelPricing("claude-sonnet-4-5-20250929", 0.003, 0.015),
    ModelPricing("claude-haiku-4-5-20251001", 0.0008, 0.004),
]

# Fallback for unknown models
_DEFAULT_FALLBACK = ModelPricing("_default", 0.003, 0.015)


class CostCalculator:
    """Calculates dollar cost from token usage and model pricing."""

    def __init__(
        self,
        pricing: list[ModelPricing] | None = None,
        default: ModelPricing | None = None,
    ) -> None:
        source = pricing if pricing is not None else DEFAULT_PRICING
        self._pricing = {p.model: p for p in source}
        self._default = default or _DEFAULT_FALLBACK

    def calculate(self, model: str, input_tokens: int, output_tokens: int) -> CostRecord:
        p = self._pricing.get(model, self._default)
        input_cost = round((input_tokens / 1000) * p.input_cost_per_1k, 6)
        output_cost = round((output_tokens / 1000) * p.output_cost_per_1k, 6)
        return CostRecord(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_cost=input_cost,
            output_cost=output_cost,
            total_cost=round(input_cost + output_cost, 6),
        )

    def from_usage(self, usage: RoleUsage) -> CostRecord:
        return self.calculate(usage.model, usage.input_tokens, usage.output_tokens)

    def calculate_batch(self, usages: list[RoleUsage]) -> list[CostRecord]:
        return [self.from_usage(u) for u in usages]
