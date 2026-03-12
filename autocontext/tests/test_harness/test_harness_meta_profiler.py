"""Tests for autocontext.harness.meta.profiler — PerformanceProfiler."""

from __future__ import annotations

import math

from autocontext.harness.meta.collector import MetricsCollector
from autocontext.harness.meta.profiler import PerformanceProfiler
from autocontext.harness.meta.types import RoleMetric


def _metric(
    role: str = "competitor",
    generation: int = 0,
    input_tokens: int = 1000,
    output_tokens: int = 500,
    latency_ms: int = 2000,
    cost: float = 0.01,
    gate_decision: str = "advance",
    score_delta: float = 0.1,
) -> RoleMetric:
    return RoleMetric(
        role=role, generation=generation, input_tokens=input_tokens,
        output_tokens=output_tokens, latency_ms=latency_ms, cost=cost,
        gate_decision=gate_decision, score_delta=score_delta,
    )


def _populated_collector() -> MetricsCollector:
    """Collector with 5 observations for 'competitor' and 3 for 'analyst'."""
    c = MetricsCollector()
    # Competitor: 5 gens, 3 advances, 2 retries
    c.add(_metric(role="competitor", generation=0, cost=0.01, gate_decision="advance", score_delta=0.15))
    c.add(_metric(role="competitor", generation=1, cost=0.012, gate_decision="advance", score_delta=0.10))
    c.add(_metric(role="competitor", generation=2, cost=0.008, gate_decision="retry", score_delta=-0.05))
    c.add(_metric(role="competitor", generation=3, cost=0.011, gate_decision="advance", score_delta=0.20))
    c.add(_metric(role="competitor", generation=4, cost=0.009, gate_decision="retry", score_delta=-0.03))
    # Analyst: 3 gens, 1 advance, 2 retries
    c.add(_metric(role="analyst", generation=0, cost=0.02, gate_decision="advance", score_delta=0.05))
    c.add(_metric(role="analyst", generation=1, cost=0.025, gate_decision="retry", score_delta=-0.10))
    c.add(_metric(role="analyst", generation=2, cost=0.018, gate_decision="retry", score_delta=-0.02))
    return c


def test_profiler_single_role_profile() -> None:
    c = _populated_collector()
    p = PerformanceProfiler(c)
    profile = p.profile("competitor")
    assert profile is not None
    assert profile.role == "competitor"
    assert profile.generations_observed == 5
    # Mean cost: (0.01 + 0.012 + 0.008 + 0.011 + 0.009) / 5 = 0.01
    assert abs(profile.mean_cost_per_gen - 0.01) < 0.001


def test_profiler_advance_rate_calculated() -> None:
    c = _populated_collector()
    p = PerformanceProfiler(c)
    profile = p.profile("competitor")
    assert profile is not None
    # 3 advances out of 5
    assert profile.advance_rate == 0.6


def test_profiler_cost_per_advance() -> None:
    c = _populated_collector()
    p = PerformanceProfiler(c)
    profile = p.profile("competitor")
    assert profile is not None
    # total_cost = 0.05, advances = 3 → 0.05/3 ≈ 0.016667
    total_cost = 0.01 + 0.012 + 0.008 + 0.011 + 0.009
    expected_cpa = total_cost / 3
    assert abs(profile.cost_per_advance - expected_cpa) < 0.001


def test_profiler_cost_per_advance_zero_advances() -> None:
    c = MetricsCollector()
    for i in range(3):
        c.add(_metric(generation=i, gate_decision="retry", score_delta=-0.1))
    p = PerformanceProfiler(c)
    profile = p.profile("competitor")
    assert profile is not None
    assert math.isinf(profile.cost_per_advance)


def test_profiler_token_efficiency() -> None:
    c = _populated_collector()
    p = PerformanceProfiler(c)
    profile = p.profile("competitor")
    assert profile is not None
    # Positive deltas: gen0 (0.15, 1500 tokens), gen1 (0.10, 1500), gen3 (0.20, 1500)
    # total positive delta = 0.45, total positive tokens = 4500
    # efficiency = 0.45 / (4500/1000) = 0.45/4.5 = 0.1
    assert abs(profile.token_efficiency - 0.1) < 0.01


def test_profiler_token_efficiency_no_positive_deltas() -> None:
    c = MetricsCollector()
    for i in range(3):
        c.add(_metric(generation=i, score_delta=-0.1))
    p = PerformanceProfiler(c)
    profile = p.profile("competitor")
    assert profile is not None
    assert profile.token_efficiency == 0.0


def test_profiler_all_profiles() -> None:
    c = _populated_collector()
    p = PerformanceProfiler(c)
    profiles = p.all_profiles()
    assert "competitor" in profiles
    assert "analyst" in profiles
    assert len(profiles) == 2


def test_profiler_profile_unknown_role() -> None:
    c = _populated_collector()
    p = PerformanceProfiler(c)
    assert p.profile("nonexistent") is None


def test_profiler_requires_minimum_observations() -> None:
    c = MetricsCollector()
    c.add(_metric(generation=0))
    c.add(_metric(generation=1))
    # Only 2 observations, default min is 3
    p = PerformanceProfiler(c)
    assert p.profile("competitor") is None
    # With min_observations=2
    p2 = PerformanceProfiler(c, min_observations=2)
    assert p2.profile("competitor") is not None


def test_profiler_most_cost_efficient_role() -> None:
    c = _populated_collector()
    p = PerformanceProfiler(c)
    ranked = p.ranked_by_efficiency()
    assert len(ranked) == 2
    # competitor has lower cost_per_advance than analyst
    assert ranked[0].role == "competitor"


def test_profiler_most_expensive_role() -> None:
    c = _populated_collector()
    p = PerformanceProfiler(c)
    ranked = p.ranked_by_cost()
    assert len(ranked) == 2
    # analyst costs more per gen than competitor
    assert ranked[0].role == "analyst"


def test_profiler_summary() -> None:
    c = _populated_collector()
    p = PerformanceProfiler(c)
    s = p.summary()
    assert "Role Performance Profiles" in s
    assert "competitor" in s
    assert "analyst" in s
    assert "Advance%" in s
