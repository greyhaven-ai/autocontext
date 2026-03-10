"""Tests for mts.harness.meta_optimizer — MetaOptimizer coordinator."""

from __future__ import annotations

from pathlib import Path

from mts.harness.audit.writer import AppendOnlyAuditWriter
from mts.harness.core.types import RoleUsage
from mts.harness.cost.tracker import CostTracker
from mts.harness.meta.advisor import ConfigAdvisor
from mts.harness.meta.collector import MetricsCollector
from mts.harness.meta.profiler import PerformanceProfiler
from mts.harness.meta_optimizer import MetaOptimizer


def _usage(model: str = "claude-sonnet-4-5-20250929", input_tokens: int = 1000, output_tokens: int = 500) -> RoleUsage:
    return RoleUsage(input_tokens=input_tokens, output_tokens=output_tokens, latency_ms=200, model=model)


def test_coordinator_disabled_is_noop() -> None:
    """When all components are None, methods are safe no-ops."""
    mo = MetaOptimizer()
    mo.record_llm_call("competitor", _usage())
    mo.record_gate_decision("advance", 0.1, 0)
    mo.record_generation(0, {"competitor": _usage()}, "advance", 0.1)
    assert mo.cost_summary() is None
    assert mo.profiles() == {}
    assert mo.recommendations() == []
    report = mo.report()
    assert "Meta-Optimization Report" in report


def test_coordinator_records_llm_call(tmp_path: Path) -> None:
    audit = AppendOnlyAuditWriter(tmp_path / "audit.ndjson")
    cost = CostTracker()
    mo = MetaOptimizer(audit_writer=audit, cost_tracker=cost)
    mo.record_llm_call("competitor", _usage(), generation=0)
    # Audit should have one entry
    entries = audit.read_all()
    assert len(entries) == 1
    assert entries[0].category.value == "llm_call"
    assert entries[0].actor == "competitor"
    assert "cost_usd" in entries[0].metadata
    # Cost tracker should have one record
    summary = cost.summary()
    assert summary.records_count == 1


def test_coordinator_records_gate_decision(tmp_path: Path) -> None:
    audit = AppendOnlyAuditWriter(tmp_path / "audit.ndjson")
    mo = MetaOptimizer(audit_writer=audit)
    mo.record_gate_decision("advance", 0.15, 0)
    entries = audit.read_all()
    assert len(entries) == 1
    assert entries[0].category.value == "gate_decision"
    assert entries[0].action == "gate_advance"
    assert entries[0].metadata["delta"] == 0.15


def test_coordinator_records_generation() -> None:
    collector = MetricsCollector()
    mo = MetaOptimizer(collector=collector)
    role_usages = {
        "competitor": _usage(input_tokens=1000, output_tokens=500),
        "analyst": _usage(input_tokens=2000, output_tokens=800),
    }
    mo.record_generation(0, role_usages, "advance", 0.1)
    assert collector.generation_count() == 1
    assert collector.roles() == {"competitor", "analyst"}


def test_coordinator_cost_summary() -> None:
    cost = CostTracker()
    mo = MetaOptimizer(cost_tracker=cost)
    mo.record_llm_call("competitor", _usage(), generation=0)
    mo.record_llm_call("analyst", _usage(), generation=0)
    summary = mo.cost_summary()
    assert summary is not None
    assert summary.records_count == 2
    assert summary.total_cost > 0


def test_coordinator_profiles() -> None:
    collector = MetricsCollector()
    profiler = PerformanceProfiler(collector, min_observations=3)
    mo = MetaOptimizer(collector=collector, profiler=profiler)
    # Need at least 3 observations per role
    for i in range(5):
        mo.record_generation(i, {"competitor": _usage()}, "advance", 0.1)
    profiles = mo.profiles()
    assert "competitor" in profiles
    assert profiles["competitor"].advance_rate == 1.0


def test_coordinator_recommendations() -> None:
    collector = MetricsCollector()
    profiler = PerformanceProfiler(collector, min_observations=3)
    advisor = ConfigAdvisor(
        profiler,
        current_config={"model_competitor": "claude-opus-4-6"},
    )
    mo = MetaOptimizer(collector=collector, profiler=profiler, advisor=advisor)
    # Feed 5 generations with high advance rate
    for i in range(5):
        mo.record_generation(i, {"competitor": _usage()}, "advance", 0.1)
    recs = mo.recommendations()
    # Should recommend downgrading from opus since advance rate is 100%
    model_recs = [r for r in recs if r.parameter == "model"]
    assert len(model_recs) >= 1


def test_coordinator_report(tmp_path: Path) -> None:
    audit = AppendOnlyAuditWriter(tmp_path / "audit.ndjson")
    cost = CostTracker()
    collector = MetricsCollector()
    profiler = PerformanceProfiler(collector, min_observations=3)
    advisor = ConfigAdvisor(profiler)
    mo = MetaOptimizer(
        audit_writer=audit, cost_tracker=cost,
        collector=collector, profiler=profiler, advisor=advisor,
    )
    mo.record_llm_call("competitor", _usage(), generation=0)
    report = mo.report()
    assert "Meta-Optimization Report" in report
    assert "Cost:" in report


def test_coordinator_from_settings() -> None:
    from mts.config.settings import AppSettings
    settings = AppSettings(
        audit_enabled=True,
        audit_log_path=Path("/tmp/test_audit.ndjson"),
        cost_tracking_enabled=True,
        cost_budget_limit=10.0,
        meta_profiling_enabled=True,
        meta_min_observations=3,
    )
    mo = MetaOptimizer.from_settings(settings)
    # Should be functional
    mo.record_llm_call("competitor", _usage())
    summary = mo.cost_summary()
    assert summary is not None
    assert summary.records_count == 1
