from __future__ import annotations

from autocontext.backpressure import BackpressureGate, GateDecision
from autocontext.backpressure.trend_gate import ScoreHistory, TrendAwareGate


def test_trend_gate_delegates_for_single_gen() -> None:
    """With no history or empty history, TrendAwareGate makes same decision as BackpressureGate."""
    gate = TrendAwareGate(min_delta=0.01)
    simple = BackpressureGate(min_delta=0.01)

    # No history
    result = gate.evaluate(0.5, 0.52, retry_count=0, max_retries=2)
    expected = simple.evaluate(0.5, 0.52, retry_count=0, max_retries=2)
    assert result.decision == expected.decision
    assert result.delta == expected.delta
    assert result.threshold == expected.threshold

    # Empty history
    empty = ScoreHistory(scores=(), gate_decisions=())
    result2 = gate.evaluate(0.5, 0.52, retry_count=0, max_retries=2, history=empty)
    assert result2.decision == expected.decision


def test_trend_gate_detects_plateau() -> None:
    """Given score history [0.5, 0.5, 0.5, 0.51] and min_delta=0.005, gate should advance
    because the effective threshold is relaxed (0.005 * 0.5 = 0.0025, delta 0.01 exceeds it).
    """
    gate = TrendAwareGate(min_delta=0.005, plateau_window=3, plateau_relaxation_factor=0.5)
    history = ScoreHistory(scores=(0.5, 0.5, 0.5, 0.51), gate_decisions=())

    result = gate.evaluate(0.5, 0.51, retry_count=0, max_retries=2, history=history)
    assert result.decision == "advance"
    # Effective threshold should be relaxed
    assert result.threshold < 0.005


def test_trend_gate_consistent_improvement() -> None:
    """Given history [0.3, 0.4, 0.5, 0.55] with min_delta=0.01, gate uses standard threshold.
    Delta is 0.05, so advance.
    """
    gate = TrendAwareGate(min_delta=0.01)
    history = ScoreHistory(scores=(0.3, 0.4, 0.5, 0.55), gate_decisions=())

    result = gate.evaluate(0.5, 0.55, retry_count=0, max_retries=2, history=history)
    assert result.decision == "advance"
    assert result.threshold == 0.01  # Standard threshold, no relaxation


def test_trend_gate_custom_metrics_in_decision() -> None:
    """Pass custom_metrics={"territory": 0.7}, verify GateDecision.metadata contains them."""
    gate = TrendAwareGate(min_delta=0.01)

    result = gate.evaluate(0.5, 0.52, retry_count=0, max_retries=2, custom_metrics={"territory": 0.7})
    assert result.metadata == {"territory": 0.7}


def test_trend_gate_consecutive_rollbacks() -> None:
    """History with consecutive rollbacks + improvement that barely misses threshold
    should still advance because consecutive rollbacks relaxed the threshold.
    """
    gate = TrendAwareGate(min_delta=0.01, consecutive_rollback_threshold=3, plateau_relaxation_factor=0.5)
    history = ScoreHistory(
        scores=(0.3, 0.3, 0.3, 0.3),
        gate_decisions=("rollback", "rollback", "rollback"),
    )

    # Delta is 0.004, which is < 0.01 but >= 0.005 (relaxed threshold)
    result = gate.evaluate(0.3, 0.306, retry_count=0, max_retries=2, history=history)
    assert result.decision == "advance"
    assert result.threshold < 0.01


def test_gate_decision_metadata_field() -> None:
    """Create GateDecision with and without metadata. Default is empty dict. Backward compatible."""
    # Without metadata (backward compatible)
    d1 = GateDecision(decision="advance", delta=0.02, threshold=0.005, reason="test")
    assert d1.metadata == {}

    # With metadata
    d2 = GateDecision(decision="advance", delta=0.02, threshold=0.005, reason="test", metadata={"score": 0.8})
    assert d2.metadata == {"score": 0.8}


def test_simple_gate_unchanged() -> None:
    """The existing BackpressureGate still works identically."""
    gate = BackpressureGate(min_delta=0.01)

    # Advance when delta >= min_delta
    result = gate.evaluate(0.5, 0.52, retry_count=0, max_retries=2)
    assert result.decision == "advance"
    assert result.delta == 0.02
    assert result.threshold == 0.01
    assert result.reason == "score improved"

    # Retry when retries available
    result = gate.evaluate(0.5, 0.505, retry_count=0, max_retries=2)
    assert result.decision == "retry"
    assert result.reason == "insufficient improvement; retry permitted"

    # Rollback when retries exhausted
    result = gate.evaluate(0.5, 0.505, retry_count=2, max_retries=2)
    assert result.decision == "rollback"
    assert result.reason == "insufficient improvement and retries exhausted"
