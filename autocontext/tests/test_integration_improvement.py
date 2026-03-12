"""Integration test: 3-round improvement cycle (MTS-30).

Validates improvement loop: agent revises based on feedback, score improves.
Uses mock task with improving scores across rounds.
"""

from __future__ import annotations

from autocontext.execution.improvement_loop import ImprovementLoop
from autocontext.scenarios.agent_task import AgentTaskInterface, AgentTaskResult


class _ImprovingMockTask(AgentTaskInterface):
    """Mock task that simulates score improvement across rounds."""

    SCORES = [0.55, 0.72, 0.88]

    def __init__(self) -> None:
        self._eval_count = 0
        self._revise_count = 0

    def get_task_prompt(self, state: dict) -> str:
        return "Write a haiku about distributed systems"

    def get_rubric(self) -> str:
        return "syllable accuracy (5-7-5), technical relevance, creativity"

    def initial_state(self, seed: int | None = None) -> dict:
        return {}

    def describe_task(self) -> str:
        return self.get_task_prompt({})

    def evaluate_output(
        self, output: str, state: dict, **kwargs: object,
    ) -> AgentTaskResult:
        score = self.SCORES[min(self._eval_count, len(self.SCORES) - 1)]
        dims = {
            "syllable_accuracy": min(1.0, score + 0.05),
            "technical_relevance": score,
            "creativity": max(0.0, score - 0.05),
        }
        self._eval_count += 1
        return AgentTaskResult(
            score=score,
            reasoning=f"Round {self._eval_count} feedback: score={score:.2f}",
            dimension_scores=dims,
        )

    def revise_output(self, output: str, judge_result: AgentTaskResult, state: dict) -> str:
        self._revise_count += 1
        return f"Revised v{self._revise_count}: improved content based on feedback"


class TestIntegrationImprovementCycle:
    """MTS-30: 3-round improvement cycle with score improvement."""

    def test_three_rounds_complete(self) -> None:
        task = _ImprovingMockTask()
        loop = ImprovementLoop(task, max_rounds=3, quality_threshold=0.95)
        result = loop.run(
            "Nodes whisper data\nConsensus slowly converges\nNetwork partition",
            {},
        )
        assert result.total_rounds == 3
        assert len(result.rounds) == 3

    def test_score_improves(self) -> None:
        task = _ImprovingMockTask()
        loop = ImprovementLoop(task, max_rounds=3, quality_threshold=0.95)
        result = loop.run("initial haiku", {})
        valid_scores = [r.score for r in result.rounds if not r.judge_failed]
        assert valid_scores[-1] > valid_scores[0], "Final score should be higher than initial"

    def test_final_better_than_initial(self) -> None:
        task = _ImprovingMockTask()
        loop = ImprovementLoop(task, max_rounds=3, quality_threshold=0.95)
        result = loop.run("initial haiku", {})
        assert result.improved

    def test_no_parse_failures(self) -> None:
        task = _ImprovingMockTask()
        loop = ImprovementLoop(task, max_rounds=3, quality_threshold=0.95)
        result = loop.run("initial haiku", {})
        assert result.judge_failures == 0

    def test_round_results_saved(self) -> None:
        task = _ImprovingMockTask()
        loop = ImprovementLoop(task, max_rounds=3, quality_threshold=0.95)
        result = loop.run("initial haiku", {})
        for r in result.rounds:
            assert r.score > 0
            assert len(r.reasoning) > 0
            assert r.round_number >= 1

    def test_dimension_trajectory_tracked(self) -> None:
        task = _ImprovingMockTask()
        loop = ImprovementLoop(task, max_rounds=3, quality_threshold=0.95)
        result = loop.run("initial haiku", {})
        assert "syllable_accuracy" in result.dimension_trajectory
        assert "technical_relevance" in result.dimension_trajectory
        assert "creativity" in result.dimension_trajectory
        assert len(result.dimension_trajectory["syllable_accuracy"]) == 3

    def test_revisions_happen(self) -> None:
        task = _ImprovingMockTask()
        loop = ImprovementLoop(task, max_rounds=3, quality_threshold=0.95)
        result = loop.run("initial haiku", {})
        revision_rounds = [r for r in result.rounds if r.is_revision]
        assert len(revision_rounds) == 2  # rounds 2 and 3 are revisions

    def test_best_score_is_highest(self) -> None:
        task = _ImprovingMockTask()
        loop = ImprovementLoop(task, max_rounds=3, quality_threshold=0.95)
        result = loop.run("initial haiku", {})
        assert result.best_score == max(r.score for r in result.rounds)
        assert result.best_round == 3
