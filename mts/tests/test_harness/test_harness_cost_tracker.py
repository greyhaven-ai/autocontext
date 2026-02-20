"""Tests for mts.harness.cost.tracker — CostTracker."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

from mts.harness.core.types import RoleUsage
from mts.harness.cost.tracker import CostTracker
from mts.harness.cost.types import CostSummary


def _usage(model: str = "claude-sonnet-4-5-20250929", input_tokens: int = 1000, output_tokens: int = 500) -> RoleUsage:
    return RoleUsage(input_tokens=input_tokens, output_tokens=output_tokens, latency_ms=200, model=model)


def test_tracker_record_adds_to_total() -> None:
    tracker = CostTracker()
    record1 = tracker.record(_usage(), role="competitor")
    record2 = tracker.record(_usage(), role="analyst")
    summary = tracker.summary()
    assert summary.total_cost == round(record1.total_cost + record2.total_cost, 6)
    assert summary.records_count == 2


def test_tracker_cost_by_role() -> None:
    tracker = CostTracker()
    tracker.record(_usage(), role="competitor")
    tracker.record(_usage(), role="competitor")
    tracker.record(_usage(), role="analyst")
    by_role = tracker.cost_by_role()
    assert "competitor" in by_role
    assert "analyst" in by_role
    # Competitor got 2 calls, analyst 1 — competitor should be ~2x analyst
    assert by_role["competitor"] > by_role["analyst"]


def test_tracker_cost_by_model() -> None:
    tracker = CostTracker()
    tracker.record(_usage(model="claude-sonnet-4-5-20250929"), role="competitor")
    tracker.record(_usage(model="claude-opus-4-6"), role="analyst")
    by_model = tracker.cost_by_model()
    assert "claude-sonnet-4-5-20250929" in by_model
    assert "claude-opus-4-6" in by_model
    assert len(by_model) == 2


def test_tracker_generation_cost() -> None:
    tracker = CostTracker()
    tracker.record(_usage(), role="competitor", generation=0)
    tracker.record(_usage(), role="analyst", generation=0)
    tracker.record(_usage(), role="competitor", generation=1)
    per_gen = tracker.cost_per_generation()
    assert len(per_gen) == 2
    assert per_gen[0][0] == 0  # gen index 0
    assert per_gen[1][0] == 1  # gen index 1
    # Gen 0 had 2 calls, gen 1 had 1 — gen 0 should cost more
    assert per_gen[0][1] > per_gen[1][1]


def test_tracker_summary() -> None:
    tracker = CostTracker()
    tracker.record(_usage(model="claude-sonnet-4-5-20250929", input_tokens=1000, output_tokens=500), role="competitor")
    tracker.record(_usage(model="claude-opus-4-6", input_tokens=2000, output_tokens=1000), role="analyst")
    summary = tracker.summary()
    assert isinstance(summary, CostSummary)
    assert summary.records_count == 2
    assert summary.total_cost > 0
    assert summary.total_input_tokens == 3000
    assert summary.total_output_tokens == 1500
    assert len(summary.cost_by_model) == 2


def test_tracker_budget_alert_callback() -> None:
    alert_fn = MagicMock()
    tracker = CostTracker(budget_limit=0.001, on_budget_alert=alert_fn)
    # Each call with 1000 input / 500 output on sonnet costs ~0.0105
    # That exceeds 0.001 on the first call
    tracker.record(_usage(), role="competitor")
    alert_fn.assert_called_once()
    args = alert_fn.call_args[0]
    assert args[0] > 0.001  # total cost exceeds limit
    assert args[1] == 0.001  # budget limit passed through


def test_tracker_no_alert_below_threshold() -> None:
    alert_fn = MagicMock()
    # Set a very high budget so it's never exceeded
    tracker = CostTracker(budget_limit=999999.0, on_budget_alert=alert_fn)
    tracker.record(_usage(), role="competitor")
    tracker.record(_usage(), role="analyst")
    alert_fn.assert_not_called()


def test_tracker_thread_safe() -> None:
    tracker = CostTracker()
    barrier = threading.Barrier(50)

    def worker() -> None:
        barrier.wait()
        tracker.record(_usage(input_tokens=1000, output_tokens=0), role="competitor")

    threads = [threading.Thread(target=worker) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    summary = tracker.summary()
    assert summary.records_count == 50
    # Each call: 1000 input tokens on sonnet = 0.003 per call, 50 calls = 0.15
    expected = round(50 * (1000 / 1000) * 0.003, 6)
    assert summary.total_cost == expected


def test_tracker_reset() -> None:
    alert_fn = MagicMock()
    tracker = CostTracker(budget_limit=0.001, on_budget_alert=alert_fn)
    tracker.record(_usage(), role="competitor")
    assert alert_fn.call_count == 1
    tracker.reset()
    summary = tracker.summary()
    assert summary.total_cost == 0.0
    assert summary.records_count == 0
    assert tracker.cost_by_role() == {}
    assert tracker.cost_by_model() == {}
    assert tracker.cost_per_generation() == []
    # After reset, alert should fire again on next breach
    tracker.record(_usage(), role="competitor")
    assert alert_fn.call_count == 2


def test_tracker_cost_per_generation() -> None:
    tracker = CostTracker()
    tracker.record(_usage(), role="competitor", generation=0)
    tracker.record(_usage(), role="analyst", generation=1)
    tracker.record(_usage(), role="coach", generation=2)
    # Also a record with no generation — should be excluded
    tracker.record(_usage(), role="architect")
    result = tracker.cost_per_generation()
    assert len(result) == 3
    assert [gen for gen, _ in result] == [0, 1, 2]
    # Each has one call with same usage, so costs should be equal
    costs = [cost for _, cost in result]
    assert costs[0] == costs[1] == costs[2]
