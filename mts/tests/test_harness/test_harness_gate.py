"""Tests for mts.harness.pipeline.gate — GateDecision, BackpressureGate."""

from __future__ import annotations

import pytest

from mts.harness.pipeline.gate import BackpressureGate, GateDecision


def test_gate_decision_frozen() -> None:
    gd = GateDecision(decision="advance", delta=0.01, threshold=0.005, reason="ok")
    with pytest.raises(AttributeError):
        gd.decision = "retry"  # type: ignore[misc]


def test_gate_advance_when_delta_exceeds_threshold() -> None:
    gate = BackpressureGate(min_delta=0.005)
    result = gate.evaluate(previous_best=0.5, current_best=0.52, retry_count=0, max_retries=3)
    assert result.decision == "advance"
    assert result.delta > 0


def test_gate_retry_when_delta_below_and_retries_remain() -> None:
    gate = BackpressureGate(min_delta=0.005)
    result = gate.evaluate(previous_best=0.5, current_best=0.501, retry_count=0, max_retries=3)
    assert result.decision == "retry"


def test_gate_rollback_when_delta_below_and_retries_exhausted() -> None:
    gate = BackpressureGate(min_delta=0.005)
    result = gate.evaluate(previous_best=0.5, current_best=0.501, retry_count=3, max_retries=3)
    assert result.decision == "rollback"


def test_gate_exact_threshold_advances() -> None:
    gate = BackpressureGate(min_delta=0.005)
    result = gate.evaluate(previous_best=0.5, current_best=0.505, retry_count=0, max_retries=3)
    assert result.decision == "advance"


def test_gate_negative_delta_retries() -> None:
    gate = BackpressureGate(min_delta=0.005)
    result = gate.evaluate(previous_best=0.5, current_best=0.49, retry_count=0, max_retries=3)
    assert result.decision == "retry"
    assert result.delta < 0
