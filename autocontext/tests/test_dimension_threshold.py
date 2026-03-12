"""Tests for MTS-43: Dimension threshold gating + worst dimension tracking."""

from __future__ import annotations

from autocontext.execution.improvement_loop import ImprovementLoop
from autocontext.scenarios.agent_task import AgentTaskInterface, AgentTaskResult


class ProgrammableTask(AgentTaskInterface):
    """Task returning pre-programmed results for each round."""

    def __init__(self, results: list[AgentTaskResult]) -> None:
        self._results = results
        self._call = 0

    def get_task_prompt(self, state: dict) -> str:
        return "test"

    def evaluate_output(
        self, output: str, state: dict,
        reference_context: str | None = None,
        required_concepts: list[str] | None = None,
        calibration_examples: list[dict] | None = None,
        **kwargs: object,
    ) -> AgentTaskResult:
        idx = min(self._call, len(self._results) - 1)
        self._call += 1
        return self._results[idx]

    def get_rubric(self) -> str:
        return "test"

    def initial_state(self, seed: int | None = None) -> dict:
        return {}

    def describe_task(self) -> str:
        return "test"

    def revise_output(self, output: str, judge_result: AgentTaskResult, state: dict) -> str:
        return f"{output} [revised]"


class TestOverallMetButDimFailsContinues:
    """Loop continues when overall >= threshold but a dimension < dim_threshold."""

    def test_overall_met_but_dim_fails_continues(self) -> None:
        # R1: score=0.85, action=0.5 (below dim_threshold 0.8) -> continue
        # R2: score=0.87, action=0.78 (still below 0.8) -> continue
        # R3: score=0.90, action=0.85 (all dims >= 0.8) -> stop
        task = ProgrammableTask([
            AgentTaskResult(
                score=0.85, reasoning="round 1",
                dimension_scores={"clarity": 0.90, "action": 0.50},
            ),
            AgentTaskResult(
                score=0.87, reasoning="round 2",
                dimension_scores={"clarity": 0.92, "action": 0.78},
            ),
            AgentTaskResult(
                score=0.90, reasoning="round 3",
                dimension_scores={"clarity": 0.95, "action": 0.85},
            ),
        ])
        loop = ImprovementLoop(
            task, max_rounds=5, quality_threshold=0.85,
            dimension_threshold=0.8,
        )
        result = loop.run("test", {})
        # Should NOT stop at round 1 or 2 because action < 0.8
        assert result.total_rounds == 3
        assert result.met_threshold is True
        assert result.termination_reason == "threshold_met"


class TestWorstDimensionTracked:
    """Verify worst_dimension and worst_dimension_score in round results."""

    def test_worst_dimension_tracked(self) -> None:
        task = ProgrammableTask([
            AgentTaskResult(
                score=0.80, reasoning="ok",
                dimension_scores={"clarity": 0.90, "accuracy": 0.70, "depth": 0.85},
            ),
            AgentTaskResult(
                score=0.95, reasoning="great",
                dimension_scores={"clarity": 0.95, "accuracy": 0.90, "depth": 0.92},
            ),
        ])
        loop = ImprovementLoop(task, max_rounds=3, quality_threshold=0.9)
        result = loop.run("test", {})

        # Round 1: worst dimension is accuracy at 0.70
        assert result.rounds[0].worst_dimension == "accuracy"
        assert result.rounds[0].worst_dimension_score == 0.70

        # Round 2: worst dimension is accuracy at 0.90
        assert result.rounds[1].worst_dimension == "accuracy"
        assert result.rounds[1].worst_dimension_score == 0.90

    def test_worst_dimension_none_without_dimensions(self) -> None:
        task = ProgrammableTask([
            AgentTaskResult(score=0.95, reasoning="great"),
        ])
        loop = ImprovementLoop(task, max_rounds=1, quality_threshold=0.9)
        result = loop.run("test", {})
        assert result.rounds[0].worst_dimension is None
        assert result.rounds[0].worst_dimension_score is None


class TestNoDimThresholdBehavesNormally:
    """Without dimension_threshold, overall threshold alone controls exit."""

    def test_no_dim_threshold_stops_early(self) -> None:
        """Without dimension_threshold, loop stops as soon as overall >= quality_threshold."""
        task = ProgrammableTask([
            AgentTaskResult(
                score=0.90, reasoning="round 1",
                dimension_scores={"clarity": 0.95, "action": 0.50},
            ),
            AgentTaskResult(
                score=0.92, reasoning="round 2",
                dimension_scores={"clarity": 0.97, "action": 0.78},
            ),
        ])
        loop = ImprovementLoop(
            task, max_rounds=5, quality_threshold=0.85,
        )
        result = loop.run("test", {})
        # 0.90 >= 0.85, clearly above (0.90 >= 0.85 + 0.02), should stop at round 1
        assert result.total_rounds == 1
        assert result.met_threshold is True
        assert result.termination_reason == "threshold_met"

    def test_with_dim_threshold_continues_past_overall(self) -> None:
        """With dimension_threshold, loop continues even when overall >= quality_threshold."""
        task = ProgrammableTask([
            AgentTaskResult(
                score=0.90, reasoning="round 1",
                dimension_scores={"clarity": 0.95, "action": 0.50},
            ),
            AgentTaskResult(
                score=0.92, reasoning="round 2",
                dimension_scores={"clarity": 0.97, "action": 0.85},
            ),
        ])
        loop = ImprovementLoop(
            task, max_rounds=5, quality_threshold=0.85,
            dimension_threshold=0.8,
        )
        result = loop.run("test", {})
        # Round 1: overall 0.90 >= 0.85 BUT action 0.50 < 0.80 -> continue
        # Round 2: overall 0.92 >= 0.85 AND all dims >= 0.80 -> stop
        assert result.total_rounds == 2
        assert result.met_threshold is True
        assert result.termination_reason == "threshold_met"
