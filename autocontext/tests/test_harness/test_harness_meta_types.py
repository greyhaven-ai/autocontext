"""Tests for autocontext.harness.meta.types — RoleMetric, RoleProfile, ConfigRecommendation."""

from __future__ import annotations

import dataclasses

from autocontext.harness.meta.types import ConfigRecommendation, RoleMetric, RoleProfile


def test_role_metric_construction() -> None:
    m = RoleMetric(
        role="competitor",
        generation=0,
        input_tokens=1000,
        output_tokens=500,
        latency_ms=2000,
        cost=0.0105,
        gate_decision="advance",
        score_delta=0.15,
    )
    assert m.role == "competitor"
    assert m.generation == 0
    assert m.input_tokens == 1000
    assert m.output_tokens == 500
    assert m.latency_ms == 2000
    assert m.cost == 0.0105
    assert m.gate_decision == "advance"
    assert m.score_delta == 0.15


def test_role_metric_frozen() -> None:
    m = RoleMetric(
        role="competitor", generation=0, input_tokens=1000, output_tokens=500,
        latency_ms=2000, cost=0.01, gate_decision="advance", score_delta=0.1,
    )
    assert dataclasses.is_dataclass(m)
    try:
        m.role = "other"  # type: ignore[misc]
        raise AssertionError("Expected FrozenInstanceError")
    except dataclasses.FrozenInstanceError:
        pass


def test_role_metric_total_tokens() -> None:
    m = RoleMetric(
        role="analyst", generation=1, input_tokens=2000, output_tokens=800,
        latency_ms=3000, cost=0.02, gate_decision="retry", score_delta=-0.05,
    )
    assert m.total_tokens == 2800


def test_role_profile_construction() -> None:
    p = RoleProfile(
        role="competitor",
        generations_observed=10,
        advance_rate=0.6,
        mean_tokens=1500.0,
        mean_latency_ms=2500.0,
        mean_cost_per_gen=0.012,
        cost_per_advance=0.02,
        token_efficiency=0.1,
    )
    assert p.role == "competitor"
    assert p.generations_observed == 10
    assert p.advance_rate == 0.6
    assert p.mean_tokens == 1500.0
    assert p.mean_latency_ms == 2500.0
    assert p.mean_cost_per_gen == 0.012
    assert p.cost_per_advance == 0.02
    assert p.token_efficiency == 0.1


def test_role_profile_frozen() -> None:
    p = RoleProfile(
        role="analyst", generations_observed=5, advance_rate=0.4,
        mean_tokens=1000.0, mean_latency_ms=2000.0, mean_cost_per_gen=0.01,
        cost_per_advance=0.025, token_efficiency=0.05,
    )
    assert dataclasses.is_dataclass(p)
    try:
        p.role = "other"  # type: ignore[misc]
        raise AssertionError("Expected FrozenInstanceError")
    except dataclasses.FrozenInstanceError:
        pass


def test_config_recommendation_construction() -> None:
    r = ConfigRecommendation(
        role="competitor",
        parameter="model",
        current_value="claude-opus-4-6",
        recommended_value="claude-sonnet-4-5-20250929",
        confidence=0.85,
        rationale="Competitor achieves similar advance rate with sonnet at 80% lower cost.",
    )
    assert r.role == "competitor"
    assert r.parameter == "model"
    assert r.current_value == "claude-opus-4-6"
    assert r.recommended_value == "claude-sonnet-4-5-20250929"
    assert r.confidence == 0.85
    assert "80% lower cost" in r.rationale


def test_config_recommendation_frozen() -> None:
    r = ConfigRecommendation(
        role="analyst", parameter="temperature", current_value="0.2",
        recommended_value="0.4", confidence=0.6, rationale="Higher creativity needed.",
    )
    assert dataclasses.is_dataclass(r)
    try:
        r.role = "other"  # type: ignore[misc]
        raise AssertionError("Expected FrozenInstanceError")
    except dataclasses.FrozenInstanceError:
        pass
