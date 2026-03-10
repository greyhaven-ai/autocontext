"""Tests for harness quality signal computation (MTS-93)."""
from __future__ import annotations

from mts.harness.evaluation.types import EvaluationResult
from mts.knowledge.harness_quality import HarnessQualitySignal, compute_harness_quality


class TestComputeHarnessQuality:
    """compute_harness_quality extracts quality metrics from match results."""

    def test_all_clean(self) -> None:
        results = [
            EvaluationResult(score=0.8, passed=True, errors=[]),
            EvaluationResult(score=0.9, passed=True, errors=[]),
        ]
        q = compute_harness_quality(results)
        assert q.total_matches == 2
        assert q.error_count == 0
        assert q.crash_count == 0
        assert q.error_rate == 0.0
        assert q.crash_rate == 0.0

    def test_with_errors(self) -> None:
        results = [
            EvaluationResult(score=0.5, passed=True, errors=["illegal move"]),
            EvaluationResult(score=0.8, passed=True, errors=[]),
            EvaluationResult(score=0.3, passed=True, errors=["invalid format"]),
        ]
        q = compute_harness_quality(results)
        assert q.error_count == 2
        assert q.crash_count == 0
        assert q.error_rate == 2 / 3

    def test_with_crashes(self) -> None:
        results = [
            EvaluationResult(score=0.0, passed=False, errors=["crash"]),
            EvaluationResult(score=0.8, passed=True, errors=[]),
        ]
        q = compute_harness_quality(results)
        assert q.crash_count == 1
        assert q.error_count == 1  # crash has errors too
        assert q.crash_rate == 0.5

    def test_empty_results(self) -> None:
        q = compute_harness_quality([])
        assert q.total_matches == 0
        assert q.error_rate == 0.0
        assert q.crash_rate == 0.0


class TestHarnessQualitySignalPrompt:
    """to_prompt_section formats quality for Curator."""

    def test_basic_prompt(self) -> None:
        q = HarnessQualitySignal(total_matches=10, error_count=2, crash_count=1)
        section = q.to_prompt_section()
        assert "## Harness Quality" in section
        assert "Error rate: 20%" in section
        assert "Crash rate: 10%" in section

    def test_prompt_with_previous(self) -> None:
        prev = HarnessQualitySignal(total_matches=10, error_count=4, crash_count=2)
        curr = HarnessQualitySignal(total_matches=10, error_count=2, crash_count=1)
        section = curr.to_prompt_section(previous=prev)
        assert "improved" in section
        assert "was 40%" in section

    def test_prompt_no_change(self) -> None:
        prev = HarnessQualitySignal(total_matches=10, error_count=2, crash_count=1)
        curr = HarnessQualitySignal(total_matches=10, error_count=2, crash_count=1)
        section = curr.to_prompt_section(previous=prev)
        assert "unchanged" in section

    def test_prompt_worse(self) -> None:
        prev = HarnessQualitySignal(total_matches=10, error_count=1, crash_count=0)
        curr = HarnessQualitySignal(total_matches=10, error_count=3, crash_count=1)
        section = curr.to_prompt_section(previous=prev)
        assert "worse" in section
