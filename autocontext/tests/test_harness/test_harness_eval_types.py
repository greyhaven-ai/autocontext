"""Tests for autocontext.harness.evaluation.types — evaluation result containers."""

from __future__ import annotations

import pytest

from autocontext.harness.evaluation.types import EvaluationLimits, EvaluationResult, EvaluationSummary


class TestEvaluationLimits:
    def test_evaluation_limits_defaults(self) -> None:
        limits = EvaluationLimits()
        assert limits.timeout_seconds == 10.0
        assert limits.max_memory_mb == 512
        assert limits.network_access is False

    def test_evaluation_limits_custom(self) -> None:
        limits = EvaluationLimits(timeout_seconds=30.0, max_memory_mb=1024, network_access=True)
        assert limits.timeout_seconds == 30.0
        assert limits.max_memory_mb == 1024
        assert limits.network_access is True

    def test_evaluation_limits_frozen(self) -> None:
        limits = EvaluationLimits()
        with pytest.raises(AttributeError):
            limits.timeout_seconds = 99.0  # type: ignore[misc]


class TestEvaluationResult:
    def test_evaluation_result_construction(self) -> None:
        result = EvaluationResult(
            score=0.85,
            passed=True,
            errors=["warning"],
            metadata={"key": "val"},
            replay_data={"moves": [1, 2]},
        )
        assert result.score == 0.85
        assert result.passed is True
        assert result.errors == ["warning"]
        assert result.metadata == {"key": "val"}
        assert result.replay_data == {"moves": [1, 2]}

    def test_evaluation_result_defaults(self) -> None:
        result = EvaluationResult(score=0.5)
        assert result.passed is True
        assert result.errors == []
        assert result.metadata == {}
        assert result.replay_data == {}

    def test_evaluation_result_frozen(self) -> None:
        result = EvaluationResult(score=0.5)
        with pytest.raises(AttributeError):
            result.score = 1.0  # type: ignore[misc]


class TestEvaluationSummary:
    def test_evaluation_summary_construction(self) -> None:
        results = [EvaluationResult(score=0.8), EvaluationResult(score=0.6)]
        summary = EvaluationSummary(
            mean_score=0.7,
            best_score=0.8,
            wins=1,
            losses=1,
            elo_after=1012.0,
            results=results,
        )
        assert summary.mean_score == 0.7
        assert summary.best_score == 0.8
        assert summary.wins == 1
        assert summary.losses == 1
        assert summary.elo_after == 1012.0
        assert len(summary.results) == 2

    def test_evaluation_summary_frozen(self) -> None:
        summary = EvaluationSummary(
            mean_score=0.7, best_score=0.8, wins=1, losses=0, elo_after=1012.0, results=[]
        )
        with pytest.raises(AttributeError):
            summary.mean_score = 0.9  # type: ignore[misc]
