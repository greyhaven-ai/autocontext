"""Tests for enriched retry prompt with failure analysis."""
from __future__ import annotations

from mts.harness.evaluation.failure_report import FailureReport
from mts.harness.evaluation.types import EvaluationResult, EvaluationSummary


def test_failure_report_injected_into_retry_context() -> None:
    """Verify FailureReport.to_prompt_context() produces content suitable for retry injection."""
    results = [
        EvaluationResult(score=0.3, passed=True, errors=[], metadata={}),
        EvaluationResult(score=0.4, passed=True, errors=[], metadata={}),
    ]
    summary = EvaluationSummary(
        mean_score=0.35, best_score=0.4, wins=0, losses=2, elo_after=990.0, results=results,
    )
    report = FailureReport.from_tournament(
        summary, previous_best=0.5, threshold=0.005, strategy={"aggression": 0.8},
    )

    retry_base = "Your previous strategy scored poorly.\n"
    enriched = retry_base + "\n" + report.to_prompt_context()

    assert "FAILURE ANALYSIS" in enriched
    assert "Previous best: 0.5000" in enriched
    assert "Current best:  0.4000" in enriched
    assert "Match 0: score=0.3000" in enriched
    assert "Match 1: score=0.4000" in enriched
    assert "Do not repeat" in enriched


def test_failure_report_includes_error_context() -> None:
    results = [
        EvaluationResult(score=0.1, passed=False, errors=["timeout", "invalid_move"], metadata={}),
    ]
    summary = EvaluationSummary(
        mean_score=0.1, best_score=0.1, wins=0, losses=1, elo_after=980.0, results=results,
    )
    report = FailureReport.from_tournament(
        summary, previous_best=0.5, threshold=0.005, strategy={},
    )
    prompt = report.to_prompt_context()
    assert "timeout" in prompt
    assert "invalid_move" in prompt
