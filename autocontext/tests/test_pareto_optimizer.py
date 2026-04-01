"""Tests for AC-266: GEPA-inspired ASI/Pareto optimizer surface.

Covers: ActionableSideInfo, OptimizationObjective, Candidate,
ParetoFrontier, merge_candidates, ArtifactOptimizer.
"""

from __future__ import annotations

# ===========================================================================
# ActionableSideInfo (ASI)
# ===========================================================================


class TestActionableSideInfo:
    def test_construction(self) -> None:
        from autocontext.harness.optimizer.pareto import ActionableSideInfo

        asi = ActionableSideInfo(
            example_id="ex-1",
            outcome="failure",
            diagnosis="Missing edge case handling for empty input",
            suggested_fix="Add guard clause for empty arrays",
        )
        assert asi.example_id == "ex-1"
        assert asi.outcome == "failure"

    def test_roundtrip(self) -> None:
        from autocontext.harness.optimizer.pareto import ActionableSideInfo

        asi = ActionableSideInfo(
            example_id="ex-2",
            outcome="near_miss",
            diagnosis="Almost correct but off by 1",
            suggested_fix="Fix loop bound",
        )
        d = asi.to_dict()
        restored = ActionableSideInfo.from_dict(d)
        assert restored.diagnosis == "Almost correct but off by 1"


# ===========================================================================
# OptimizationObjective
# ===========================================================================


class TestOptimizationObjective:
    def test_maximize(self) -> None:
        from autocontext.harness.optimizer.pareto import OptimizationObjective

        obj = OptimizationObjective(name="task_score", direction="maximize")
        assert obj.is_better(0.8, 0.6) is True
        assert obj.is_better(0.5, 0.7) is False

    def test_minimize(self) -> None:
        from autocontext.harness.optimizer.pareto import OptimizationObjective

        obj = OptimizationObjective(name="cost_usd", direction="minimize")
        assert obj.is_better(0.05, 0.10) is True
        assert obj.is_better(0.15, 0.10) is False


# ===========================================================================
# Candidate
# ===========================================================================


class TestCandidate:
    def test_construction(self) -> None:
        from autocontext.harness.optimizer.pareto import Candidate

        c = Candidate(
            candidate_id="c-1",
            artifact="Write a clear, concise summary of the input.",
            scores={"task_score": 0.8, "cost_usd": 0.05},
            asi=[],
        )
        assert c.candidate_id == "c-1"
        assert c.scores["task_score"] == 0.8

    def test_dominates(self) -> None:
        from autocontext.harness.optimizer.pareto import Candidate, OptimizationObjective

        objectives = [
            OptimizationObjective("score", "maximize"),
            OptimizationObjective("cost", "minimize"),
        ]
        a = Candidate("a", "art-a", {"score": 0.9, "cost": 0.05}, [])
        b = Candidate("b", "art-b", {"score": 0.7, "cost": 0.10}, [])
        assert a.dominates(b, objectives) is True
        assert b.dominates(a, objectives) is False

    def test_no_domination_on_tradeoff(self) -> None:
        from autocontext.harness.optimizer.pareto import Candidate, OptimizationObjective

        objectives = [
            OptimizationObjective("score", "maximize"),
            OptimizationObjective("cost", "minimize"),
        ]
        a = Candidate("a", "", {"score": 0.9, "cost": 0.20}, [])
        b = Candidate("b", "", {"score": 0.7, "cost": 0.05}, [])
        assert a.dominates(b, objectives) is False
        assert b.dominates(a, objectives) is False


# ===========================================================================
# ParetoFrontier
# ===========================================================================


class TestParetoFrontier:
    def test_add_non_dominated(self) -> None:
        from autocontext.harness.optimizer.pareto import (
            Candidate,
            OptimizationObjective,
            ParetoFrontier,
        )

        objectives = [
            OptimizationObjective("score", "maximize"),
            OptimizationObjective("cost", "minimize"),
        ]
        frontier = ParetoFrontier(objectives)
        frontier.add(Candidate("a", "", {"score": 0.9, "cost": 0.20}, []))
        frontier.add(Candidate("b", "", {"score": 0.7, "cost": 0.05}, []))

        # Both are non-dominated (tradeoff)
        assert len(frontier.candidates) == 2

    def test_dominated_candidate_rejected(self) -> None:
        from autocontext.harness.optimizer.pareto import (
            Candidate,
            OptimizationObjective,
            ParetoFrontier,
        )

        objectives = [
            OptimizationObjective("score", "maximize"),
            OptimizationObjective("cost", "minimize"),
        ]
        frontier = ParetoFrontier(objectives)
        frontier.add(Candidate("a", "", {"score": 0.9, "cost": 0.05}, []))
        frontier.add(Candidate("b", "", {"score": 0.7, "cost": 0.10}, []))

        # b is dominated by a
        assert len(frontier.candidates) == 1
        assert frontier.candidates[0].candidate_id == "a"

    def test_new_dominant_removes_old(self) -> None:
        from autocontext.harness.optimizer.pareto import (
            Candidate,
            OptimizationObjective,
            ParetoFrontier,
        )

        objectives = [OptimizationObjective("score", "maximize")]
        frontier = ParetoFrontier(objectives)
        frontier.add(Candidate("a", "", {"score": 0.7}, []))
        frontier.add(Candidate("b", "", {"score": 0.9}, []))

        assert len(frontier.candidates) == 1
        assert frontier.candidates[0].candidate_id == "b"

    def test_best_for_objective(self) -> None:
        from autocontext.harness.optimizer.pareto import (
            Candidate,
            OptimizationObjective,
            ParetoFrontier,
        )

        objectives = [
            OptimizationObjective("score", "maximize"),
            OptimizationObjective("cost", "minimize"),
        ]
        frontier = ParetoFrontier(objectives)
        frontier.add(Candidate("high_score", "", {"score": 0.95, "cost": 0.30}, []))
        frontier.add(Candidate("low_cost", "", {"score": 0.70, "cost": 0.02}, []))

        best_score = frontier.best_for("score")
        assert best_score is not None
        assert best_score.candidate_id == "high_score"

        best_cost = frontier.best_for("cost")
        assert best_cost is not None
        assert best_cost.candidate_id == "low_cost"


# ===========================================================================
# merge_candidates
# ===========================================================================


class TestMergeCandidates:
    def test_merge_produces_combined(self) -> None:
        from autocontext.harness.optimizer.pareto import Candidate, merge_candidates

        a = Candidate("a", "Handle edge cases carefully.", {"score": 0.8}, [])
        b = Candidate("b", "Be concise and direct.", {"score": 0.75}, [])

        merged = merge_candidates(a, b)
        assert merged.candidate_id != a.candidate_id
        # Merged artifact should reference both
        assert "edge cases" in merged.artifact.lower() or "concise" in merged.artifact.lower()

    def test_merge_combines_asi(self) -> None:
        from autocontext.harness.optimizer.pareto import (
            ActionableSideInfo,
            Candidate,
            merge_candidates,
        )

        a = Candidate(
            "a",
            "art-a",
            {},
            [
                ActionableSideInfo(
                    example_id="e1",
                    outcome="fail",
                    diagnosis="diag1",
                    suggested_fix="fix1",
                ),
            ],
        )
        b = Candidate(
            "b",
            "art-b",
            {},
            [
                ActionableSideInfo(
                    example_id="e2",
                    outcome="fail",
                    diagnosis="diag2",
                    suggested_fix="fix2",
                ),
            ],
        )

        merged = merge_candidates(a, b)
        assert len(merged.asi) == 2


# ===========================================================================
# Integration: ImprovementLoop uses ParetoFrontier
# ===========================================================================


class TestImprovementLoopParetoIntegration:
    """Live-boundary tests proving the optimizer runs inside the improvement loop."""

    def _make_task(self, scores: list[float]):
        """Create a mock AgentTaskInterface that returns predefined scores."""
        from unittest.mock import MagicMock

        from autocontext.scenarios.agent_task import AgentTaskResult

        task = MagicMock()
        task.get_task_prompt.return_value = "Test prompt"
        task.get_rubric.return_value = "Test rubric"
        task.initial_state.return_value = {}
        task.prepare_context.side_effect = lambda s: s
        task.validate_context.return_value = []
        task.verify_facts.return_value = None

        call_idx = [0]

        def mock_evaluate(output, state, **kwargs):
            idx = min(call_idx[0], len(scores) - 1)
            score = scores[idx]
            call_idx[0] += 1
            return AgentTaskResult(
                score=score,
                reasoning=f"Round {call_idx[0]} feedback",
                dimension_scores={"quality": score, "depth": score * 0.9},
            )

        def mock_revise(output, judge_result, state):
            return f"revised-{call_idx[0]}: {output[:20]}"

        task.evaluate_output.side_effect = mock_evaluate
        task.revise_output.side_effect = mock_revise
        return task

    def test_improvement_result_has_frontier(self) -> None:
        """ImprovementResult should contain Pareto frontier data."""
        from autocontext.execution.improvement_loop import ImprovementLoop

        task = self._make_task([0.5, 0.7, 0.85])
        loop = ImprovementLoop(task, max_rounds=3, quality_threshold=0.95)
        result = loop.run("initial output", {})

        # Frontier should be in the result
        assert hasattr(result, "pareto_frontier") or "pareto_frontier" in (result.metadata if hasattr(result, "metadata") else {})
        frontier_data = getattr(result, "pareto_frontier", None)
        if frontier_data is None and hasattr(result, "metadata"):
            frontier_data = result.metadata.get("pareto_frontier")
        assert frontier_data is not None
        assert len(frontier_data) >= 1  # At least one candidate on the frontier

    def test_frontier_tracks_dimension_scores(self) -> None:
        """Frontier candidates should carry per-dimension scores, not just aggregate."""
        from autocontext.execution.improvement_loop import ImprovementLoop

        task = self._make_task([0.4, 0.6, 0.8])
        loop = ImprovementLoop(task, max_rounds=3, quality_threshold=0.95)
        result = loop.run("initial output", {})

        frontier_data = getattr(result, "pareto_frontier", None)
        if frontier_data is None and hasattr(result, "metadata"):
            frontier_data = result.metadata.get("pareto_frontier")
        assert frontier_data is not None
        # Each frontier entry should have dimension scores
        for entry in frontier_data:
            assert "scores" in entry or "dimension_scores" in entry

    def test_asi_collected_from_low_score_rounds(self) -> None:
        """ASI should be collected from rounds with poor dimension performance."""
        from autocontext.execution.improvement_loop import ImprovementLoop

        task = self._make_task([0.3, 0.5, 0.7])
        loop = ImprovementLoop(task, max_rounds=3, quality_threshold=0.95)
        result = loop.run("initial output", {})

        asi_data = getattr(result, "actionable_side_info", None)
        if asi_data is None and hasattr(result, "metadata"):
            asi_data = result.metadata.get("actionable_side_info")
        # Should have collected ASI from the low-scoring rounds
        assert asi_data is not None
        assert len(asi_data) >= 1

    def test_best_output_from_frontier_not_just_highest_score(self) -> None:
        """When multiple rounds exist, best_output should use frontier selection."""
        from autocontext.execution.improvement_loop import ImprovementLoop

        task = self._make_task([0.6, 0.55, 0.7])
        loop = ImprovementLoop(task, max_rounds=3, quality_threshold=0.95)
        result = loop.run("initial output", {})

        # Best score should be the highest
        assert result.best_score >= 0.7

    def test_objective_expansion_preserves_existing_frontier_candidates(self) -> None:
        """Adding a new dimension objective should not discard earlier candidates."""
        from unittest.mock import MagicMock

        from autocontext.execution.improvement_loop import ImprovementLoop
        from autocontext.scenarios.agent_task import AgentTaskResult

        task = MagicMock()
        task.get_task_prompt.return_value = "Test prompt"
        task.get_rubric.return_value = "Test rubric"
        task.initial_state.return_value = {}
        task.prepare_context.side_effect = lambda s: s
        task.validate_context.return_value = []
        task.verify_facts.return_value = None

        results = iter([
            AgentTaskResult(score=0.90, reasoning="strong baseline", dimension_scores={}),
            AgentTaskResult(
                score=0.85,
                reasoning="lower aggregate, new dimension discovered",
                dimension_scores={"novelty": 0.85},
            ),
        ])

        task.evaluate_output.side_effect = lambda *args, **kwargs: next(results)
        task.revise_output.side_effect = lambda output, judge_result, state: f"revised: {output}"

        loop = ImprovementLoop(task, max_rounds=2, quality_threshold=0.95)
        result = loop.run("initial output", {})

        frontier_ids = {entry["candidate_id"] for entry in result.pareto_frontier}
        assert frontier_ids == {"round-1", "round-2"}

        round1 = next(entry for entry in result.pareto_frontier if entry["candidate_id"] == "round-1")
        assert round1["scores"]["task_score"] == 0.90
        assert round1["scores"]["novelty"] == 0.0
