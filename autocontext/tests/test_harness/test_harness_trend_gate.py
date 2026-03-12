"""Tests for autocontext.harness.pipeline.trend_gate — ScoreHistory, TrendAwareGate."""

from __future__ import annotations

import pytest

from autocontext.harness.pipeline.trend_gate import ScoreHistory, TrendAwareGate


def test_trend_gate_delegates_to_simple_without_history() -> None:
    gate = TrendAwareGate(min_delta=0.005)
    result = gate.evaluate(previous_best=0.5, current_best=0.52, retry_count=0, max_retries=3)
    assert result.decision == "advance"


def test_trend_gate_plateau_relaxes_threshold() -> None:
    gate = TrendAwareGate(min_delta=0.01, plateau_window=3, plateau_relaxation_factor=0.5)
    # History shows plateau: scores barely change
    history = ScoreHistory(scores=(0.50, 0.501, 0.502, 0.501), gate_decisions=("advance", "retry", "retry"))
    # Delta of 0.006 < 0.01 (normal threshold) but >= 0.005 (relaxed)
    result = gate.evaluate(
        previous_best=0.5, current_best=0.506, retry_count=0, max_retries=3, history=history
    )
    assert result.decision == "advance"


def test_trend_gate_consecutive_rollbacks_relax_threshold() -> None:
    gate = TrendAwareGate(min_delta=0.01, consecutive_rollback_threshold=3, plateau_relaxation_factor=0.5)
    history = ScoreHistory(scores=(0.5,), gate_decisions=("rollback", "rollback", "rollback"))
    result = gate.evaluate(
        previous_best=0.5, current_best=0.506, retry_count=0, max_retries=3, history=history
    )
    assert result.decision == "advance"


def test_trend_gate_custom_metrics_in_metadata() -> None:
    gate = TrendAwareGate(min_delta=0.005)
    metrics = {"win_rate": 0.75, "avg_score": 0.6}
    result = gate.evaluate(
        previous_best=0.5, current_best=0.52, retry_count=0, max_retries=3, custom_metrics=metrics
    )
    assert result.metadata == metrics


def test_score_history_frozen() -> None:
    sh = ScoreHistory(scores=(0.5, 0.6), gate_decisions=("advance",))
    with pytest.raises(AttributeError):
        sh.scores = (0.7,)  # type: ignore[misc]
