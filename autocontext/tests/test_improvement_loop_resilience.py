"""Tests for ImprovementLoop resilience to judge parse failures (MTS-13)."""

from __future__ import annotations

from autocontext.execution.improvement_loop import (
    ImprovementLoop,
    ImprovementResult,
    RoundResult,
    _is_parse_failure,
)
from autocontext.scenarios.agent_task import AgentTaskInterface, AgentTaskResult


class FakeTask(AgentTaskInterface):
    """Fake task that returns configurable judge results."""

    def __init__(self, eval_results: list[AgentTaskResult], revision_fn=None):
        self._results = eval_results
        self._call_count = 0
        self._revision_fn = revision_fn or (lambda out, res, st: f"{out} [revised]")

    def get_task_prompt(self, state: dict) -> str:
        return "test prompt"

    def evaluate_output(self, output, state, **kwargs) -> AgentTaskResult:
        idx = min(self._call_count, len(self._results) - 1)
        self._call_count += 1
        return self._results[idx]

    def revise_output(self, output, result, state) -> str:
        return self._revision_fn(output, result, state)

    def get_rubric(self) -> str:
        return "test rubric"

    def initial_state(self, seed: int | None = None) -> dict:
        return {}

    def describe_task(self) -> str:
        return "test task"


class TestIsParseFailure:
    def test_real_zero_score(self):
        assert not _is_parse_failure(0.0, "Terrible output with no redeeming qualities")

    def test_real_nonzero_score(self):
        assert not _is_parse_failure(0.5, "no parseable score found")

    def test_missing_markers(self):
        assert _is_parse_failure(0.0, "Failed to parse judge response: missing JUDGE_RESULT markers")

    def test_invalid_json(self):
        assert _is_parse_failure(0.0, "Failed to parse judge response: invalid JSON")

    def test_no_parseable(self):
        assert _is_parse_failure(0.0, "Failed to parse judge response: no parseable score found")


class TestLoopResilience:
    def test_judge_failure_not_counted_as_best(self):
        """Parse failure should not set best_score to 0.0 and poison best tracking."""
        task = FakeTask([
            AgentTaskResult(score=0.0, reasoning="Failed to parse judge response: no parseable score found"),
            AgentTaskResult(score=0.75, reasoning="Good output"),
        ])
        loop = ImprovementLoop(task, max_rounds=3, quality_threshold=0.9)
        result = loop.run("initial", {})
        assert result.best_score == 0.75
        assert result.judge_failures == 1
        assert any(r.judge_failed for r in result.rounds)

    def test_judge_failure_carries_forward_feedback(self):
        """When judge fails, last good feedback should be used for revision."""
        revisions = []

        def track_revision(output, result, state):
            revisions.append(result.reasoning)
            return f"{output} [revised]"

        task = FakeTask(
            [
                AgentTaskResult(score=0.6, reasoning="Needs more detail"),
                AgentTaskResult(score=0.0, reasoning="Failed to parse judge response: no parseable score found"),
                AgentTaskResult(score=0.85, reasoning="Much better"),
            ],
            revision_fn=track_revision,
        )
        loop = ImprovementLoop(task, max_rounds=4, quality_threshold=0.9)
        result = loop.run("initial", {})
        # The revision after failure should use "Needs more detail" (last good)
        assert "Needs more detail" in revisions[1]
        assert result.judge_failures == 1

    def test_consecutive_failures_abort(self):
        """3 consecutive judge failures should abort the loop."""
        task = FakeTask([
            AgentTaskResult(score=0.0, reasoning="Failed to parse judge response: no parseable score found"),
        ] * 5)
        loop = ImprovementLoop(task, max_rounds=10, quality_threshold=0.9)
        result = loop.run("initial", {})
        assert result.judge_failures == 3
        assert result.total_rounds == 3
        assert not result.met_threshold

    def test_failure_then_recovery(self):
        """Loop should continue after a single failure followed by success."""
        task = FakeTask([
            AgentTaskResult(score=0.5, reasoning="OK start"),
            AgentTaskResult(score=0.0, reasoning="Failed to parse judge response: invalid JSON"),
            AgentTaskResult(score=0.95, reasoning="Excellent"),
        ])
        loop = ImprovementLoop(task, max_rounds=5, quality_threshold=0.9)
        result = loop.run("initial", {})
        assert result.met_threshold
        assert result.best_score == 0.95
        assert result.judge_failures == 1

    def test_failure_on_first_round_no_prior_feedback(self):
        """Judge failure on round 1 with no prior feedback should just retry next round."""
        task = FakeTask([
            AgentTaskResult(score=0.0, reasoning="Failed to parse judge response: no parseable score found"),
            AgentTaskResult(score=0.8, reasoning="Nice"),
        ])
        loop = ImprovementLoop(task, max_rounds=3, quality_threshold=0.9)
        result = loop.run("initial", {})
        assert result.best_score == 0.8
        assert result.judge_failures == 1

    def test_improved_property_ignores_failures(self):
        """ImprovementResult.improved should only compare valid rounds."""
        task = FakeTask([
            AgentTaskResult(score=0.5, reasoning="Start"),
            AgentTaskResult(score=0.0, reasoning="Failed to parse judge response: no parseable score found"),
            AgentTaskResult(score=0.7, reasoning="Better"),
        ])
        loop = ImprovementLoop(task, max_rounds=4, quality_threshold=0.9)
        result = loop.run("initial", {})
        assert result.improved  # 0.7 > 0.5

    def test_no_failures_unchanged_behavior(self):
        """Normal operation without failures should work exactly as before."""
        task = FakeTask([
            AgentTaskResult(score=0.6, reasoning="OK"),
            AgentTaskResult(score=0.95, reasoning="Great"),
        ])
        loop = ImprovementLoop(task, max_rounds=3, quality_threshold=0.9)
        result = loop.run("initial", {})
        assert result.met_threshold
        assert result.best_score == 0.95
        assert result.judge_failures == 0
        assert not any(r.judge_failed for r in result.rounds)

    def test_judge_failure_field_default(self):
        """RoundResult.judge_failed should default to False."""
        r = RoundResult(round_number=1, output="x", score=0.5, reasoning="ok")
        assert r.judge_failed is False

    def test_improvement_result_judge_failures_default(self):
        """ImprovementResult.judge_failures should default to 0."""
        r = ImprovementResult(
            rounds=[], best_output="x", best_score=0.5,
            best_round=1, total_rounds=1, met_threshold=False,
        )
        assert r.judge_failures == 0
