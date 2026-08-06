"""Verifier gate caching in the improvement loop (AC-902).

A byte-identical artifact must never be re-judged (oscillating revisions
included), repeated cached failures terminate the loop, and a missing
required target fails the round closed without consulting the judge.
"""

from __future__ import annotations

from autocontext.execution.improvement_loop import ImprovementLoop
from autocontext.scenarios.agent_task import AgentTaskInterface, AgentTaskResult


class OscillatingTask(AgentTaskInterface):
    """Revisions cycle A -> B -> A -> B; every evaluation scores low."""

    def __init__(self) -> None:
        self.evaluate_calls: list[str] = []

    def get_task_prompt(self, state):  # type: ignore[no-untyped-def]
        return "task"

    def evaluate_output(self, output, state, reference_context=None, required_concepts=None, **kwargs):  # type: ignore[no-untyped-def]
        self.evaluate_calls.append(output)
        return AgentTaskResult(score=0.3, reasoning="needs work", dimension_scores={})

    def revise_output(self, output, judge_result, state):  # type: ignore[no-untyped-def]
        return "output B" if output == "output A" else "output A"

    def describe_task(self):  # type: ignore[no-untyped-def]
        return "test task"

    def get_rubric(self):  # type: ignore[no-untyped-def]
        return "rubric"

    def initial_state(self):  # type: ignore[no-untyped-def]
        return {}


class TargetTask(AgentTaskInterface):
    def __init__(self) -> None:
        self.evaluate_calls = 0

    def get_task_prompt(self, state):  # type: ignore[no-untyped-def]
        return "task"

    def evaluate_output(self, output, state, reference_context=None, required_concepts=None, **kwargs):  # type: ignore[no-untyped-def]
        self.evaluate_calls += 1
        return AgentTaskResult(score=0.95, reasoning="great", dimension_scores={})

    def revise_output(self, output, judge_result, state):  # type: ignore[no-untyped-def]
        return output + " theorem foo := rfl"

    def describe_task(self):  # type: ignore[no-untyped-def]
        return "test task"

    def get_rubric(self):  # type: ignore[no-untyped-def]
        return "rubric"

    def initial_state(self):  # type: ignore[no-untyped-def]
        return {}


class TestUnchangedArtifactCaching:
    def test_oscillating_revisions_reuse_cached_verdicts(self) -> None:
        task = OscillatingTask()
        events: list[str] = []
        loop = ImprovementLoop(task, max_rounds=6, quality_threshold=0.9, on_event=lambda e: events.append(e.event))
        result = loop.run("output A", {})
        # Only two distinct artifacts exist; the judge must run at most twice.
        assert len(task.evaluate_calls) == 2
        assert "verifier_cache_hit" in events
        # flat repeats stop early: either the plateau detector (identical
        # scores) or the cached-failure backstop fires; both are correct
        assert result.termination_reason in ("plateau_stall", "unchanged_output")
        assert result.total_rounds <= 4
        cache_stats = result.metadata["verifier_cache"]
        assert cache_stats["hits"] >= 1 and cache_stats["entries"] == 2

    def test_score_diverse_oscillation_terminates_via_cache_backstop(self) -> None:
        class DivergingOscillator(OscillatingTask):
            def evaluate_output(self, output, state, reference_context=None, required_concepts=None, **kwargs):  # type: ignore[no-untyped-def]
                self.evaluate_calls.append(output)
                score = 0.3 if output == "output A" else 0.6
                return AgentTaskResult(score=score, reasoning="needs work", dimension_scores={})

        task = DivergingOscillator()
        loop = ImprovementLoop(task, max_rounds=8, quality_threshold=0.9)
        result = loop.run("output A", {})
        # scores alternate 0.3/0.6 so plateau detection cannot fire; the
        # cached-failure backstop must stop the A-B-A-B cycle
        assert len(task.evaluate_calls) == 2
        assert result.termination_reason == "unchanged_output"
        assert result.total_rounds == 4

    def test_changed_artifacts_always_reevaluate(self) -> None:
        task = TargetTask()
        loop = ImprovementLoop(task, max_rounds=3, quality_threshold=0.99, min_rounds=3)
        loop.run("seed", {})
        # every revision appends content, so every round is a fresh fingerprint
        assert task.evaluate_calls == 3


class TestRequiredTargets:
    def test_missing_target_fails_closed_without_judge(self) -> None:
        task = TargetTask()
        events: list[str] = []
        loop = ImprovementLoop(
            task,
            max_rounds=2,
            quality_threshold=0.9,
            required_targets=["theorem foo"],
            on_event=lambda e: events.append(e.event),
        )
        result = loop.run("empty artifact with no target", {})
        assert "targets_missing" in events
        first_round = result.rounds[0]
        assert first_round.score == 0.0
        assert "theorem foo" in first_round.reasoning
        # round 1 must not consult the judge; round 2 (revision adds the
        # target) must reach it and pass
        assert task.evaluate_calls == 1
        assert result.best_score == 0.95

    def test_present_target_goes_to_judge(self) -> None:
        task = TargetTask()
        loop = ImprovementLoop(task, max_rounds=1, quality_threshold=0.9, required_targets=["theorem foo"])
        result = loop.run("theorem foo := by simp", {})
        assert task.evaluate_calls == 1
        assert result.best_score == 0.95
