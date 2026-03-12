"""Tests for autocontext.harness.cost.calculator — CostCalculator."""

from __future__ import annotations

from autocontext.harness.core.types import RoleUsage
from autocontext.harness.cost.calculator import DEFAULT_PRICING, CostCalculator
from autocontext.harness.cost.types import ModelPricing


def test_calculator_known_model() -> None:
    calc = CostCalculator()
    record = calc.calculate("claude-sonnet-4-5-20250929", input_tokens=1000, output_tokens=500)
    # sonnet: 0.003/1k input, 0.015/1k output
    assert record.model == "claude-sonnet-4-5-20250929"
    assert record.input_tokens == 1000
    assert record.output_tokens == 500
    assert record.input_cost == round((1000 / 1000) * 0.003, 6)
    assert record.output_cost == round((500 / 1000) * 0.015, 6)
    assert record.total_cost == round(record.input_cost + record.output_cost, 6)


def test_calculator_unknown_model_uses_default() -> None:
    calc = CostCalculator()
    record = calc.calculate("unknown-model-v1", input_tokens=2000, output_tokens=1000)
    # default fallback: 0.003/1k input, 0.015/1k output
    assert record.model == "unknown-model-v1"
    assert record.input_cost == round((2000 / 1000) * 0.003, 6)
    assert record.output_cost == round((1000 / 1000) * 0.015, 6)


def test_calculator_zero_tokens() -> None:
    calc = CostCalculator()
    record = calc.calculate("claude-sonnet-4-5-20250929", input_tokens=0, output_tokens=0)
    assert record.input_cost == 0.0
    assert record.output_cost == 0.0
    assert record.total_cost == 0.0


def test_calculator_from_usage() -> None:
    calc = CostCalculator()
    usage = RoleUsage(input_tokens=1000, output_tokens=500, latency_ms=200, model="claude-sonnet-4-5-20250929")
    record = calc.from_usage(usage)
    assert record.model == "claude-sonnet-4-5-20250929"
    assert record.input_tokens == 1000
    assert record.output_tokens == 500
    assert record.total_cost == round(record.input_cost + record.output_cost, 6)


def test_calculator_batch() -> None:
    calc = CostCalculator()
    usages = [
        RoleUsage(input_tokens=1000, output_tokens=500, latency_ms=100, model="claude-sonnet-4-5-20250929"),
        RoleUsage(input_tokens=2000, output_tokens=1000, latency_ms=200, model="claude-opus-4-6"),
    ]
    records = calc.calculate_batch(usages)
    assert len(records) == 2
    assert records[0].model == "claude-sonnet-4-5-20250929"
    assert records[1].model == "claude-opus-4-6"


def test_calculator_default_pricing_includes_claude_models() -> None:
    model_names = {p.model for p in DEFAULT_PRICING}
    assert "claude-sonnet-4-5-20250929" in model_names
    assert "claude-opus-4-6" in model_names
    assert "claude-haiku-4-5-20251001" in model_names


def test_calculator_custom_pricing() -> None:
    custom = [ModelPricing("my-model", 0.01, 0.05)]
    calc = CostCalculator(pricing=custom)
    record = calc.calculate("my-model", input_tokens=1000, output_tokens=1000)
    assert record.input_cost == round((1000 / 1000) * 0.01, 6)
    assert record.output_cost == round((1000 / 1000) * 0.05, 6)


def test_calculator_cost_precision() -> None:
    calc = CostCalculator()
    # Use values that could produce floating point noise
    record = calc.calculate("claude-sonnet-4-5-20250929", input_tokens=333, output_tokens=777)
    # Verify costs are rounded to 6 decimal places
    assert record.input_cost == round(record.input_cost, 6)
    assert record.output_cost == round(record.output_cost, 6)
    assert record.total_cost == round(record.total_cost, 6)
    # Verify the string representation doesn't exceed 6 decimal places
    parts = str(record.input_cost).split(".")
    if len(parts) == 2:
        assert len(parts[1]) <= 6
