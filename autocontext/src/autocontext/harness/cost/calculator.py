"""Cost calculator — converts token usage into dollar amounts."""
from __future__ import annotations

from autocontext.harness.core.types import RoleUsage
from autocontext.harness.cost.types import CostRecord, ModelPricing

# Dollars per 1K tokens, refreshed 2026-08-11 alongside the model-id sweep.
#
# Sourced from OpenRouter's live catalog, which is what was reachable at the
# time; OpenRouter adds margin over vendor-direct list prices, so treat these
# as an upper bound rather than an invoice. The previous table was left on
# Opus 4.6 rates ($15/$75 per M) -- Opus 5 is $5/$25, so cost attribution for
# every default run had been overstating spend by roughly 3x.
DEFAULT_PRICING: list[ModelPricing] = [
    ModelPricing("claude-fable-5", 0.010, 0.050),
    ModelPricing("claude-opus-5", 0.005, 0.025),
    ModelPricing("claude-sonnet-5", 0.002, 0.010),
    ModelPricing("claude-haiku-4-5-20251001", 0.001, 0.005),
    ModelPricing("gpt-5.6-sol", 0.005, 0.030),
    ModelPricing("gpt-5.6-terra", 0.001, 0.006),
    ModelPricing("gpt-5.6-luna", 0.0001, 0.0006),
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
