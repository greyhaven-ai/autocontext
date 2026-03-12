"""Tests for autocontext.harness.evaluation.protocol — Evaluator protocol compliance."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from autocontext.harness.evaluation.protocol import Evaluator
from autocontext.harness.evaluation.types import EvaluationLimits, EvaluationResult


class _DummyEvaluator:
    """Concrete evaluator for protocol compliance testing."""

    def evaluate(
        self,
        candidate: Mapping[str, Any],
        seed: int,
        limits: EvaluationLimits,
    ) -> EvaluationResult:
        return EvaluationResult(score=float(seed) / 100.0)


class TestEvaluatorProtocol:
    def test_evaluator_protocol_callable(self) -> None:
        evaluator: Evaluator = _DummyEvaluator()
        assert hasattr(evaluator, "evaluate")

    def test_evaluator_returns_evaluation_result(self) -> None:
        evaluator: Evaluator = _DummyEvaluator()
        result = evaluator.evaluate({"key": "val"}, seed=50, limits=EvaluationLimits())
        assert isinstance(result, EvaluationResult)
        assert result.score == 0.5
