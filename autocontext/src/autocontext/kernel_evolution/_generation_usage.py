"""Shared validation for provider token-usage aliases."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Annotated, Protocol

from pydantic import BeforeValidator, FiniteFloat

# This table is part of the ``estimated-model-pricing-v1`` receipt contract.
# Keep it local and immutable so later updates to the application's default
# pricing cannot change the replay result for an already-issued receipt.
_GENERATION_PRICING_V1: dict[str, tuple[float, float]] = {
    "claude-fable-5": (0.010, 0.050),
    "claude-opus-5": (0.005, 0.025),
    "claude-sonnet-5": (0.003, 0.015),
    "claude-haiku-4-5-20251001": (0.001, 0.005),
    "claude-opus-4-6": (0.005, 0.025),
    "claude-sonnet-4-5-20250929": (0.003, 0.015),
    "gpt-5.6-sol": (0.005, 0.030),
    "gpt-5.6-terra": (0.0025, 0.015),
    "gpt-5.6-luna": (0.001, 0.006),
}
_GENERATION_FALLBACK_PRICING_V1 = (0.003, 0.015)
_GENERATION_MODEL_ALIASES_V1 = {"gpt-5.6-sol-pro": "gpt-5.6-sol"}
_KNOWN_USAGE_COUNTERS = {
    "input_tokens",
    "output_tokens",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
}


def _exact_number(value: object) -> object:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("generation accounting values must be exact numbers")
    return value


ExactFiniteFloat = Annotated[FiniteFloat, BeforeValidator(_exact_number)]


class _GenerationUsage(Protocol):
    input_tokens: int
    output_tokens: int
    total_tokens: int
    provider_usage: dict[str, int]


class _CostedGenerationRecord(Protocol):
    @property
    def model(self) -> str: ...

    @property
    def cost_usd(self) -> float: ...

    @property
    def cost_source(self) -> str: ...

    @property
    def usage(self) -> _GenerationUsage: ...


class _GenerationBudgetState(Protocol):
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: float
    wall_seconds: float


class _GenerationBudget(Protocol):
    max_total_input_tokens: int
    max_total_output_tokens: int
    max_total_tokens: int
    max_cost_usd: float
    max_wall_seconds: float


def exhausted_generation_budgets(
    state: _GenerationBudgetState,
    budget: _GenerationBudget,
) -> list[str]:
    limits = (
        (state.input_tokens, budget.max_total_input_tokens, "input_tokens"),
        (state.output_tokens, budget.max_total_output_tokens, "output_tokens"),
        (state.total_tokens, budget.max_total_tokens, "total_tokens"),
        (float(state.cost_usd), float(budget.max_cost_usd), "cost_usd"),
        (float(state.wall_seconds), float(budget.max_wall_seconds), "wall_seconds"),
    )
    return [name for used, limit, name in limits if used >= limit]


def validate_directional_token_aliases(usage: Mapping[str, int]) -> None:
    for canonical, alias in (("input_tokens", "prompt_tokens"), ("output_tokens", "completion_tokens")):
        if canonical in usage and alias in usage and usage[canonical] != usage[alias]:
            raise ValueError("provider directional token aliases disagree")


def estimated_generation_cost(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
) -> float:
    """Price unallocated provider tokens at the conservative output-token rate."""
    unallocated = max(0, total_tokens - input_tokens - output_tokens)
    candidate = model.split("/", 1)[1] if model.startswith(("anthropic/", "google/", "openai/")) else model
    canonical_model = _GENERATION_MODEL_ALIASES_V1.get(candidate, candidate)
    input_rate, output_rate = _GENERATION_PRICING_V1.get(
        canonical_model,
        _GENERATION_FALLBACK_PRICING_V1,
    )
    input_cost = round((input_tokens / 1000) * input_rate, 6)
    output_cost = round(((output_tokens + unallocated) / 1000) * output_rate, 6)
    return round(input_cost + output_cost, 6)


def validate_estimated_generation_cost(record: _CostedGenerationRecord) -> None:
    if record.cost_source != "estimated-model-pricing-v1":
        return
    usage = record.usage
    expected = estimated_generation_cost(
        record.model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
    )
    if float(record.cost_usd) != expected:
        raise ValueError("generation estimated cost does not replay from its model and token usage")


def validate_generation_usage_receipt(usage: _GenerationUsage, *, require_directional: bool) -> None:
    provider_usage = usage.provider_usage
    validate_directional_token_aliases(provider_usage)
    if any(value > 0 for key, value in provider_usage.items() if key not in _KNOWN_USAGE_COUNTERS):
        raise ValueError("generation usage contains an unsupported nonzero provider counter")
    if {"input_tokens", "output_tokens"} <= provider_usage.keys():
        input_tokens = provider_usage["input_tokens"]
        output_tokens = provider_usage["output_tokens"]
    elif {"prompt_tokens", "completion_tokens"} <= provider_usage.keys():
        input_tokens = provider_usage["prompt_tokens"]
        output_tokens = provider_usage["completion_tokens"]
    else:
        directional = {"input_tokens", "output_tokens", "prompt_tokens", "completion_tokens"}
        total_only = (
            not require_directional
            and not (directional & provider_usage.keys())
            and usage.input_tokens == usage.output_tokens == 0
            and usage.total_tokens == provider_usage.get("total_tokens", 0)
        )
        if not total_only:
            raise ValueError("generation usage lacks replayable directional provider counters")
        return
    total_tokens = provider_usage.get("total_tokens", input_tokens + output_tokens)
    if (usage.input_tokens, usage.output_tokens, usage.total_tokens) != (input_tokens, output_tokens, total_tokens):
        raise ValueError("normalized generation usage disagrees with its provider counters")


def generation_usage_allows_retry(usage: _GenerationUsage) -> bool:
    """Require a complete provider counter family before another paid dispatch."""
    try:
        validate_generation_usage_receipt(usage, require_directional=True)
    except ValueError:
        return False
    return True


def measured_retry_sleep(
    sleep: Callable[[float], None],
    monotonic: Callable[[], float],
    delay: float,
) -> tuple[float, BaseException | None]:
    """Run one backoff while retaining its actual elapsed time and any interruption."""
    started = monotonic()
    try:
        sleep(delay)
    except BaseException as exc:
        return max(delay, monotonic() - started), exc
    return max(delay, monotonic() - started), None


__all__ = [
    "ExactFiniteFloat",
    "estimated_generation_cost",
    "exhausted_generation_budgets",
    "generation_usage_allows_retry",
    "measured_retry_sleep",
    "validate_directional_token_aliases",
    "validate_estimated_generation_cost",
    "validate_generation_usage_receipt",
]
