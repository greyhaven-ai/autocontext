"""Tests for per-task metrics tracking (MTS-55).

Verifies that ImprovementLoop results include:
- duration_ms on ImprovementResult
- judge_calls count on ImprovementResult
- round_duration_ms on each RoundResult
"""

from __future__ import annotations

from autocontext.execution.improvement_loop import ImprovementLoop
from autocontext.scenarios.agent_task import AgentTaskInterface, AgentTaskResult


class _FixedScoreTask(AgentTaskInterface):
    """Minimal task that returns a fixed score each round."""

    def __init__(self, scores: list[float]) -> None:
        self._scores = scores
        self._call = 0

    def get_task_prompt(self, state: dict) -> str:
        return "test"

    def evaluate_output(
        self,
        output: str,
        state: dict,
        reference_context: str | None = None,
        required_concepts: list[str] | None = None,
        calibration_examples: list[dict] | None = None,
        **kwargs: object,
    ) -> AgentTaskResult:
        idx = min(self._call, len(self._scores) - 1)
        self._call += 1
        return AgentTaskResult(score=self._scores[idx], reasoning="ok")

    def get_rubric(self) -> str:
        return "test"

    def initial_state(self, seed: int | None = None) -> dict:
        return {}

    def describe_task(self) -> str:
        return "test"

    def revise_output(self, output: str, judge_result: AgentTaskResult, state: dict) -> str:
        return output + " [revised]"


class TestResultHasDurationMs:
    def test_result_has_duration_ms(self) -> None:
        task = _FixedScoreTask([0.5])
        loop = ImprovementLoop(task, max_rounds=1, quality_threshold=0.9)
        result = loop.run("hello", {})
        assert result.duration_ms is not None
        assert isinstance(result.duration_ms, int)
        assert result.duration_ms >= 0


class TestResultHasJudgeCallsCount:
    def test_result_has_judge_calls_count(self) -> None:
        task = _FixedScoreTask([0.4, 0.5, 0.95])
        loop = ImprovementLoop(task, max_rounds=3, quality_threshold=0.9)
        result = loop.run("hello", {})
        # One evaluate_output call per round
        assert result.judge_calls == result.total_rounds


class TestPerRoundTiming:
    def test_per_round_timing(self) -> None:
        task = _FixedScoreTask([0.4, 0.95])
        loop = ImprovementLoop(task, max_rounds=3, quality_threshold=0.9)
        result = loop.run("hello", {})
        assert len(result.rounds) >= 1
        for rr in result.rounds:
            assert rr.round_duration_ms is not None
            assert isinstance(rr.round_duration_ms, int)
            assert rr.round_duration_ms >= 0
