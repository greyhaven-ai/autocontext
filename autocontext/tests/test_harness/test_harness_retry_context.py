"""Tests for autocontext.harness.pipeline.retry_context — RetryContext."""

from __future__ import annotations

import pytest

from autocontext.harness.pipeline.retry_context import RetryContext


def test_retry_context_fields() -> None:
    ctx = RetryContext(
        attempt=2,
        previous_score=0.45,
        best_score_needed=0.50,
        gate_threshold=0.005,
        previous_strategy={"aggression": 0.5},
        gate_reason="insufficient improvement",
    )
    assert ctx.attempt == 2
    assert ctx.previous_score == 0.45
    assert ctx.best_score_needed == 0.50
    assert ctx.gate_threshold == 0.005
    assert ctx.previous_strategy == {"aggression": 0.5}
    assert ctx.gate_reason == "insufficient improvement"


def test_retry_context_frozen() -> None:
    ctx = RetryContext(
        attempt=1,
        previous_score=0.4,
        best_score_needed=0.5,
        gate_threshold=0.005,
        previous_strategy={},
        gate_reason="test",
    )
    with pytest.raises(AttributeError):
        ctx.attempt = 3  # type: ignore[misc]
