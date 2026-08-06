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


class EpochTask(AgentTaskInterface):
    """Judge carries an evaluator epoch (production AC-885 shape)."""

    def __init__(self) -> None:
        self.evaluate_calls = 0

    def get_task_prompt(self, state):  # type: ignore[no-untyped-def]
        return "task"

    def evaluate_output(self, output, state, reference_context=None, required_concepts=None, **kwargs):  # type: ignore[no-untyped-def]
        self.evaluate_calls += 1
        return AgentTaskResult(score=0.85, reasoning="good", dimension_scores={}, evaluator_epoch="epoch-E")

    def revise_output(self, output, judge_result, state):  # type: ignore[no-untyped-def]
        return "artifact without the target"

    def describe_task(self):  # type: ignore[no-untyped-def]
        return "test task"

    def get_rubric(self):  # type: ignore[no-untyped-def]
        return "rubric"

    def initial_state(self):  # type: ignore[no-untyped-def]
        return {}


class TestReviewRegressions:
    def test_targets_missing_round_cannot_become_best_under_epoch_judge(self) -> None:
        """A synthesized round must not trigger AC-885 rebaselining and crown
        the known-bad artifact as best (review-caught Critical)."""
        task = EpochTask()
        loop = ImprovementLoop(
            task,
            max_rounds=2,
            quality_threshold=0.95,
            required_targets=["theorem foo"],
        )
        result = loop.run("theorem foo := by simp", {})
        assert result.best_score == 0.85
        assert result.best_output == "theorem foo := by simp"

    def test_cached_vetoed_verdict_replays_as_veto(self) -> None:
        """AC-750: a cached veto-zeroed score must not become the unvetoed
        delta baseline on replay."""
        from autocontext.execution.output_verifier import OutputVerifier

        class OscillatingGoodTask(AgentTaskInterface):
            def __init__(self) -> None:
                self.outputs = iter(["output B", "output A", "output C"])

            def get_task_prompt(self, state):  # type: ignore[no-untyped-def]
                return "task"

            def evaluate_output(self, output, state, reference_context=None, required_concepts=None, **kwargs):  # type: ignore[no-untyped-def]
                score = {"output A": 0.9, "output B": 0.6, "output C": 0.9}[output]
                return AgentTaskResult(score=score, reasoning="r", dimension_scores={})

            def revise_output(self, output, judge_result, state):  # type: ignore[no-untyped-def]
                return next(self.outputs)

            def describe_task(self):  # type: ignore[no-untyped-def]
                return "t"

            def get_rubric(self):  # type: ignore[no-untyped-def]
                return "rubric"

            def initial_state(self):  # type: ignore[no-untyped-def]
                return {}

        # verifier rejects output A only
        verifier = OutputVerifier(command="test-cmd")

        def fake_run(output):  # type: ignore[no-untyped-def]
            from autocontext.execution.output_verifier import VerifyResult

            ok = output != "output A"
            return VerifyResult(ok=ok, exit_code=0 if ok else 1, stdout="", stderr="" if ok else "rejected A")

        verifier.run = fake_run  # type: ignore[method-assign]
        loop = ImprovementLoop(
            OscillatingGoodTask(),
            max_rounds=4,
            quality_threshold=0.95,
            cap_score_jumps=True,
            output_verifier=verifier,
        )
        result = loop.run("output A", {})
        # round 4 (output C, 0.9, verifier passes) must win uncapped: the
        # cached vetoed replay of A in round 3 is not a legitimate baseline
        assert result.best_score == 0.9
        assert result.best_output == "output C"


class FactPenalizedOscillator(AgentTaskInterface):
    """Judge scores 0.72; fact-check penalizes to 0.648, below the 0.7 gate."""

    def __init__(self) -> None:
        self.evaluate_calls: list[str] = []

    def get_task_prompt(self, state):  # type: ignore[no-untyped-def]
        return "task"

    def evaluate_output(self, output, state, reference_context=None, required_concepts=None, **kwargs):  # type: ignore[no-untyped-def]
        self.evaluate_calls.append(output)
        return AgentTaskResult(score=0.72, reasoning="plausible", dimension_scores={})

    def verify_facts(self, output, state):  # type: ignore[no-untyped-def]
        return {"verified": False, "issues": ["claim X unsupported"]}

    def revise_output(self, output, judge_result, state):  # type: ignore[no-untyped-def]
        return "output B" if output == "output A" else "output A"

    def describe_task(self):  # type: ignore[no-untyped-def]
        return "test task"

    def get_rubric(self):  # type: ignore[no-untyped-def]
        return "rubric"

    def initial_state(self):  # type: ignore[no-untyped-def]
        return {}


class CapTriggerTask(AgentTaskInterface):
    """A scores 0.3, B scores 0.9; revisions cycle A -> B -> A -> B."""

    def __init__(self) -> None:
        self.evaluate_calls: list[str] = []

    def get_task_prompt(self, state):  # type: ignore[no-untyped-def]
        return "task"

    def evaluate_output(self, output, state, reference_context=None, required_concepts=None, **kwargs):  # type: ignore[no-untyped-def]
        self.evaluate_calls.append(output)
        return AgentTaskResult(
            score=0.3 if output == "output A" else 0.9,
            reasoning="judged",
            dimension_scores={},
        )

    def revise_output(self, output, judge_result, state):  # type: ignore[no-untyped-def]
        return "output B" if output == "output A" else "output A"

    def describe_task(self):  # type: ignore[no-untyped-def]
        return "test task"

    def get_rubric(self):  # type: ignore[no-untyped-def]
        return "rubric"

    def initial_state(self):  # type: ignore[no-untyped-def]
        return {}


class TestCachedScoreIsArtifactIntrinsic:
    """AC-902 review fix: the cache stores the artifact-intrinsic verdict.

    Fact-check penalty and veto zeroing depend only on the artifact and are
    embedded; the delta-cap clamp depends on the previous round's baseline
    (evaluation context) and must be re-derived at replay time, never frozen.
    """

    def test_fact_check_penalty_survives_cache_replay(self) -> None:
        task = FactPenalizedOscillator()
        loop = ImprovementLoop(task, max_rounds=6, quality_threshold=0.7)
        result = loop.run("output A", {})
        assert len(task.evaluate_calls) == 2
        # a cached replay must not resurrect the unpenalized 0.72 judge score
        # (which would clear the 0.7 gate the artifact honestly failed)
        for round_result in result.rounds:
            assert abs(round_result.score - 0.648) < 1e-9
        assert abs(result.best_score - 0.648) < 1e-9

    def test_cap_clamp_is_not_frozen_into_cache(self) -> None:
        task = CapTriggerTask()
        loop = ImprovementLoop(
            task,
            max_rounds=4,
            quality_threshold=0.95,
            cap_score_jumps=True,
            max_score_delta=0.2,
        )
        result = loop.run("output A", {})
        assert len(task.evaluate_calls) == 2
        # round 2 judged B at 0.9 (effective capped to 0.5 against the 0.3
        # baseline); round 4 replays B and must carry the intrinsic 0.9, not
        # the frozen clamp from round 2's context
        replayed_b = result.rounds[3]
        assert abs(replayed_b.score - 0.9) < 1e-9
