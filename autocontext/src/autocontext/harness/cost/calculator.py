"""Cost calculator — converts token usage into dollar amounts."""
from __future__ import annotations

from autocontext.harness.core.types import RoleUsage
from autocontext.harness.cost.types import CostRecord, ModelPricing

# Dollars per 1K tokens, refreshed against vendor list prices on 2026-08-12.
# Sonnet 5 uses its stable $3/$15 rate instead of the $2/$10 introductory rate
# that expires on 2026-08-31; cost guards must not silently become optimistic.
# Provider-prefixed OpenRouter ids are normalized to these entries below, so
# this remains an estimate rather than a provider invoice.
DEFAULT_PRICING: list[ModelPricing] = [
    ModelPricing("claude-fable-5", 0.010, 0.050),
    ModelPricing("claude-opus-5", 0.005, 0.025),
    ModelPricing("claude-sonnet-5", 0.003, 0.015),
    ModelPricing("claude-haiku-4-5-20251001", 0.001, 0.005),
    # Retain supported legacy ids for existing pinned configurations.
    ModelPricing("claude-opus-4-6", 0.005, 0.025),
    ModelPricing("claude-sonnet-4-5-20250929", 0.003, 0.015),
    ModelPricing("gpt-5.6-sol", 0.005, 0.030),
    ModelPricing("gpt-5.6-terra", 0.0025, 0.015),
    ModelPricing("gpt-5.6-luna", 0.001, 0.006),
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
        p = self._pricing.get(model)
        if p is None:
            p = self._pricing.get(_canonical_model_id(model), self._default)
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


_MODEL_ALIASES = {
    # OpenRouter implements pro as a serving mode on the Sol model rather than
    # a distinct OpenAI model id; token prices are still attributed to Sol.
    "gpt-5.6-sol-pro": "gpt-5.6-sol",
}


def _canonical_model_id(model: str) -> str:
    candidate = model
    if model.startswith(("anthropic/", "google/", "openai/")):
        candidate = model.split("/", 1)[1]
    return _MODEL_ALIASES.get(candidate, candidate)
