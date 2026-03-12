"""Tests for structured failure reports."""
from __future__ import annotations

from autocontext.harness.evaluation.failure_report import FailureReport
from autocontext.harness.evaluation.types import EvaluationResult, EvaluationSummary


def test_build_from_tournament() -> None:
    results = [
        EvaluationResult(score=0.3, passed=True, errors=[], metadata={}),
        EvaluationResult(score=0.6, passed=True, errors=[], metadata={}),
        EvaluationResult(score=0.2, passed=False, errors=["timeout"], metadata={}),
    ]
    summary = EvaluationSummary(
        mean_score=0.367, best_score=0.6, wins=1, losses=2, elo_after=990.0, results=results,
    )
    report = FailureReport.from_tournament(
        summary, previous_best=0.7, threshold=0.005, strategy={"aggression": 0.8},
    )
    assert len(report.match_diagnoses) == 3
    assert report.overall_delta < 0.005  # 0.6 - 0.7 = -0.1


def test_report_to_prompt_context() -> None:
    results = [
        EvaluationResult(score=0.3, passed=True, errors=[], metadata={}),
    ]
    summary = EvaluationSummary(
        mean_score=0.3, best_score=0.3, wins=0, losses=1, elo_after=990.0, results=results,
    )
    report = FailureReport.from_tournament(
        summary, previous_best=0.5, threshold=0.005, strategy={"aggression": 0.8},
    )
    prompt = report.to_prompt_context()
    assert "FAILURE ANALYSIS" in prompt
    assert "0.3" in prompt


def test_empty_errors_still_produces_report() -> None:
    results = [
        EvaluationResult(score=0.45, passed=True, errors=[], metadata={}),
    ]
    summary = EvaluationSummary(
        mean_score=0.45, best_score=0.45, wins=0, losses=1, elo_after=995.0, results=results,
    )
    report = FailureReport.from_tournament(
        summary, previous_best=0.5, threshold=0.005, strategy={},
    )
    assert report.to_prompt_context() != ""


def test_strategy_summary_truncated() -> None:
    long_strategy = {f"key_{i}": f"value_{i}" for i in range(100)}
    results = [EvaluationResult(score=0.5, passed=True, errors=[], metadata={})]
    summary = EvaluationSummary(
        mean_score=0.5, best_score=0.5, wins=0, losses=0, elo_after=1000.0, results=results,
    )
    report = FailureReport.from_tournament(
        summary, previous_best=0.5, threshold=0.005, strategy=long_strategy,
    )
    # Truncated to 200 chars + "..." ellipsis indicator
    assert len(report.strategy_summary) <= 203
    assert report.strategy_summary.endswith("...")
