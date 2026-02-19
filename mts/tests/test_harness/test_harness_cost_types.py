"""Tests for mts.harness.cost.types — ModelPricing, CostRecord, CostSummary."""

from __future__ import annotations

import dataclasses

from mts.harness.cost.types import CostRecord, CostSummary, ModelPricing


def test_model_pricing_construction() -> None:
    p = ModelPricing(model="claude-sonnet-4-5-20250929", input_cost_per_1k=0.003, output_cost_per_1k=0.015)
    assert p.model == "claude-sonnet-4-5-20250929"
    assert p.input_cost_per_1k == 0.003
    assert p.output_cost_per_1k == 0.015


def test_model_pricing_frozen() -> None:
    p = ModelPricing(model="m", input_cost_per_1k=0.01, output_cost_per_1k=0.05)
    try:
        p.model = "other"  # type: ignore[misc]
        raise AssertionError("Expected FrozenInstanceError")
    except dataclasses.FrozenInstanceError:
        pass


def test_cost_record_construction() -> None:
    r = CostRecord(
        model="claude-sonnet-4-5-20250929",
        input_tokens=1000,
        output_tokens=500,
        input_cost=0.003,
        output_cost=0.0075,
        total_cost=0.0105,
    )
    assert r.model == "claude-sonnet-4-5-20250929"
    assert r.input_tokens == 1000
    assert r.output_tokens == 500
    assert r.input_cost == 0.003
    assert r.output_cost == 0.0075
    assert r.total_cost == 0.0105


def test_cost_record_frozen() -> None:
    r = CostRecord(model="m", input_tokens=0, output_tokens=0, input_cost=0.0, output_cost=0.0, total_cost=0.0)
    try:
        r.model = "other"  # type: ignore[misc]
        raise AssertionError("Expected FrozenInstanceError")
    except dataclasses.FrozenInstanceError:
        pass


def test_cost_record_to_dict() -> None:
    r = CostRecord(
        model="claude-sonnet-4-5-20250929",
        input_tokens=1000,
        output_tokens=500,
        input_cost=0.003,
        output_cost=0.0075,
        total_cost=0.0105,
    )
    d = r.to_dict()
    assert d == {
        "model": "claude-sonnet-4-5-20250929",
        "input_tokens": 1000,
        "output_tokens": 500,
        "input_cost": 0.003,
        "output_cost": 0.0075,
        "total_cost": 0.0105,
    }


def test_cost_summary_construction() -> None:
    s = CostSummary(
        total_cost=0.05,
        total_input_tokens=5000,
        total_output_tokens=2000,
        records_count=3,
        cost_by_model={"claude-sonnet-4-5-20250929": 0.05},
    )
    assert s.total_cost == 0.05
    assert s.total_input_tokens == 5000
    assert s.total_output_tokens == 2000
    assert s.records_count == 3
    assert s.cost_by_model == {"claude-sonnet-4-5-20250929": 0.05}


def test_cost_summary_from_records() -> None:
    r1 = CostRecord(model="sonnet", input_tokens=1000, output_tokens=500, input_cost=0.003, output_cost=0.0075, total_cost=0.0105)
    r2 = CostRecord(model="sonnet", input_tokens=2000, output_tokens=1000, input_cost=0.006, output_cost=0.015, total_cost=0.021)
    r3 = CostRecord(model="opus", input_tokens=500, output_tokens=200, input_cost=0.0075, output_cost=0.015, total_cost=0.0225)

    s = CostSummary.from_records([r1, r2, r3])

    assert s.records_count == 3
    assert s.total_input_tokens == 3500
    assert s.total_output_tokens == 1700
    assert s.total_cost == round(0.0105 + 0.021 + 0.0225, 6)
    assert s.cost_by_model["sonnet"] == 0.0105 + 0.021
    assert s.cost_by_model["opus"] == 0.0225


def test_cost_summary_from_records_empty() -> None:
    s = CostSummary.from_records([])
    assert s.total_cost == 0.0
    assert s.total_input_tokens == 0
    assert s.total_output_tokens == 0
    assert s.records_count == 0
    assert s.cost_by_model == {}
