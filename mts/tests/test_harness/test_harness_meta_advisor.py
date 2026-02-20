"""Tests for mts.harness.meta.advisor — ConfigAdvisor."""

from __future__ import annotations

from mts.harness.meta.advisor import AdvisorConfig, ConfigAdvisor
from mts.harness.meta.collector import MetricsCollector
from mts.harness.meta.profiler import PerformanceProfiler
from mts.harness.meta.types import RoleMetric


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


def _high_advance_collector(role: str = "competitor", n: int = 5) -> MetricsCollector:
    """Collector with high advance rate (80%) for a role."""
    c = MetricsCollector()
    for i in range(n):
        gate = "advance" if i < int(n * 0.8) else "retry"
        delta = 0.1 if gate == "advance" else -0.05
        c.add(_metric(role=role, generation=i, gate_decision=gate, score_delta=delta))
    return c


def _low_advance_collector(role: str = "analyst", n: int = 5) -> MetricsCollector:
    """Collector with low advance rate (20%) for a role."""
    c = MetricsCollector()
    for i in range(n):
        gate = "advance" if i < int(n * 0.2) else "retry"
        delta = 0.1 if gate == "advance" else -0.05
        c.add(_metric(role=role, generation=i, gate_decision=gate, score_delta=delta))
    return c


def test_advisor_no_profiles_no_recommendations() -> None:
    c = MetricsCollector()
    p = PerformanceProfiler(c, min_observations=3)
    advisor = ConfigAdvisor(p)
    recs = advisor.recommend()
    assert recs == []


def test_advisor_recommends_model_downgrade() -> None:
    c = _high_advance_collector("competitor", n=5)
    p = PerformanceProfiler(c, min_observations=3)
    advisor = ConfigAdvisor(
        p,
        current_config={"model_competitor": "claude-opus-4-6"},
    )
    recs = advisor.recommend()
    model_recs = [r for r in recs if r.parameter == "model" and r.role == "competitor"]
    assert len(model_recs) >= 1
    assert model_recs[0].recommended_value == "claude-sonnet-4-5-20250929"


def test_advisor_no_downgrade_when_advance_rate_low() -> None:
    c = _low_advance_collector("competitor", n=5)
    p = PerformanceProfiler(c, min_observations=3)
    advisor = ConfigAdvisor(
        p,
        current_config={"model_competitor": "claude-opus-4-6"},
    )
    recs = advisor.recommend()
    downgrade_recs = [r for r in recs if r.parameter == "model" and "cheaper" in r.rationale.lower() or "cheaper model" in r.rationale.lower()]
    # Should not have a downgrade recommendation
    assert len(downgrade_recs) == 0


def test_advisor_recommends_model_upgrade() -> None:
    c = _low_advance_collector("analyst", n=5)
    p = PerformanceProfiler(c, min_observations=3)
    advisor = ConfigAdvisor(
        p,
        current_config={"model_analyst": "claude-haiku-4-5-20251001"},
    )
    recs = advisor.recommend()
    model_recs = [r for r in recs if r.parameter == "model" and r.role == "analyst" and "more capable" in r.rationale]
    assert len(model_recs) >= 1
    assert model_recs[0].recommended_value == "claude-sonnet-4-5-20250929"


def test_advisor_no_upgrade_when_advance_rate_high() -> None:
    c = _high_advance_collector("analyst", n=5)
    p = PerformanceProfiler(c, min_observations=3)
    advisor = ConfigAdvisor(
        p,
        current_config={"model_analyst": "claude-haiku-4-5-20251001"},
    )
    recs = advisor.recommend()
    upgrade_recs = [r for r in recs if r.parameter == "model" and "more capable" in r.rationale]
    assert len(upgrade_recs) == 0


def test_advisor_recommends_cadence_increase() -> None:
    # Create a role with high cost_per_advance
    c = MetricsCollector()
    for i in range(5):
        # Only 1 advance out of 5 but each costs a lot
        gate = "advance" if i == 0 else "retry"
        delta = 0.1 if gate == "advance" else -0.05
        c.add(_metric(role="architect", generation=i, cost=0.50, gate_decision=gate, score_delta=delta))
    p = PerformanceProfiler(c, min_observations=3)
    advisor = ConfigAdvisor(p, config=AdvisorConfig(high_cost_per_advance=0.5))
    recs = advisor.recommend()
    cadence_recs = [r for r in recs if r.parameter == "cadence" and r.role == "architect"]
    assert len(cadence_recs) >= 1
    assert "every 2-3 generations" in cadence_recs[0].recommended_value


def test_advisor_recommendations_have_confidence() -> None:
    c = _high_advance_collector("competitor", n=5)
    p = PerformanceProfiler(c, min_observations=3)
    advisor = ConfigAdvisor(
        p,
        current_config={"model_competitor": "claude-opus-4-6"},
    )
    recs = advisor.recommend()
    for r in recs:
        assert 0.0 <= r.confidence <= 1.0


def test_advisor_recommendations_have_rationale() -> None:
    c = _high_advance_collector("competitor", n=5)
    p = PerformanceProfiler(c, min_observations=3)
    advisor = ConfigAdvisor(
        p,
        current_config={"model_competitor": "claude-opus-4-6"},
    )
    recs = advisor.recommend()
    for r in recs:
        assert len(r.rationale) > 0


def test_advisor_custom_thresholds() -> None:
    c = MetricsCollector()
    # 3 advances out of 5 = 60% advance rate
    for i in range(5):
        gate = "advance" if i < 3 else "retry"
        delta = 0.1 if gate == "advance" else -0.05
        c.add(_metric(role="competitor", generation=i, gate_decision=gate, score_delta=delta))
    p = PerformanceProfiler(c, min_observations=3)

    # Default threshold is 0.7, so 60% shouldn't trigger downgrade
    advisor_default = ConfigAdvisor(p, current_config={"model_competitor": "claude-opus-4-6"})
    recs_default = advisor_default.recommend()
    downgrade_default = [r for r in recs_default if r.parameter == "model" and "cheaper" in r.rationale]
    assert len(downgrade_default) == 0

    # Custom threshold at 0.5, so 60% should trigger downgrade
    advisor_custom = ConfigAdvisor(
        p,
        current_config={"model_competitor": "claude-opus-4-6"},
        config=AdvisorConfig(high_advance_rate=0.5),
    )
    recs_custom = advisor_custom.recommend()
    downgrade_custom = [r for r in recs_custom if r.parameter == "model" and "cheaper" in r.rationale]
    assert len(downgrade_custom) >= 1


def test_advisor_summary() -> None:
    c = _high_advance_collector("competitor", n=5)
    p = PerformanceProfiler(c, min_observations=3)
    advisor = ConfigAdvisor(
        p,
        current_config={"model_competitor": "claude-opus-4-6"},
    )
    s = advisor.summary()
    assert "Configuration Recommendations" in s
    assert "competitor" in s
