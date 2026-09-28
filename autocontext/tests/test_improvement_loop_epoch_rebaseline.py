from __future__ import annotations

import json

from autocontext.execution.evaluator_epoch import EVALUATOR_EPOCH_REBASELINE
from autocontext.execution.improvement_events import ImprovementLoopEvent
from autocontext.execution.improvement_loop import ImprovementLoop
from autocontext.execution.judge import LLMJudge
from autocontext.execution.judge_spec import JudgeServingSpec
from autocontext.execution.task_runner import SimpleAgentTask
from autocontext.providers.base import CompletionResult, LLMProvider
from autocontext.scenarios.agent_task import AgentTaskResult


class _ScriptedJudgeProvider(LLMProvider):
    """Fake provider: judge calls return the next scripted score (with an ``accuracy`` dimension)
    served by the next scripted model; revision calls return a fresh draft. No network."""

    def __init__(self, scores: list[float], served_models: list[str]) -> None:
        self._scores = scores
        self._served_models = served_models
        self._judge_calls = 0
        self._revisions = 0

    def complete(self, system_prompt, user_prompt, model=None, temperature=0.0, max_tokens=4096, output_schema=None):
        if "expert judge" in system_prompt:
            score = self._scores[self._judge_calls]
            served_model = self._served_models[self._judge_calls]
            self._judge_calls += 1
            verdict = {"score": score, "reasoning": "scripted", "dimensions": {"accuracy": score}}
            return CompletionResult(text=json.dumps(verdict), model=served_model)
        self._revisions += 1
        return CompletionResult(text=f"revision {self._revisions}", model=model)

    def default_model(self) -> str:
        return "judge-a"


class _EpochSwapTask:
    """Round 1 scored under epoch e1 (score 0.9), round 2 under epoch e2 (score 0.4)."""

    def __init__(self) -> None:
        self._n = 0

    def get_rubric(self) -> str:
        return "rubric"

    def describe_task(self) -> str:
        return "t"

    def initial_state(self, seed: int | None = None) -> dict:
        return {}

    def get_task_prompt(self, state: dict) -> str:
        return "do it"

    def evaluate_output(self, output, state, **kwargs) -> AgentTaskResult:
        self._n += 1
        if self._n == 1:
            return AgentTaskResult(score=0.9, reasoning="e1", evaluator_epoch="e1")
        return AgentTaskResult(score=0.4, reasoning="e2", evaluator_epoch="e2")

    def revise_output(self, output, feedback, state) -> str:
        return output + " revised"

    def verify_facts(self, output, state):
        return None


class _ThresholdEpochSwapTask:
    """Every round barely meets the 0.9 threshold (0.91), but round 1 is scored
    under epoch e1 and rounds 2+ under epoch e2. Reproduces the reviewer's case:
    a prior-epoch threshold-met round must not confirm a new-epoch round as stable.
    """

    def __init__(self) -> None:
        self._n = 0

    def get_rubric(self) -> str:
        return "rubric"

    def describe_task(self) -> str:
        return "t"

    def initial_state(self, seed: int | None = None) -> dict:
        return {}

    def get_task_prompt(self, state: dict) -> str:
        return "do it"

    def evaluate_output(self, output, state, **kwargs) -> AgentTaskResult:
        self._n += 1
        epoch = "e1" if self._n == 1 else "e2"
        return AgentTaskResult(score=0.91, reasoning=epoch, evaluator_epoch=epoch)

    def revise_output(self, output, feedback, state) -> str:
        return output + f" r{self._n}"

    def verify_facts(self, output, state):
        return None


def test_rebaseline_resets_near_threshold_stability_state() -> None:
    """On epoch rebaseline the near-threshold "confirmed stable" tracker must reset
    so a prior-epoch threshold-met round cannot confirm the first new-epoch round.
    """
    loop = ImprovementLoop(
        _ThresholdEpochSwapTask(),
        max_rounds=3,
        quality_threshold=0.9,
        min_rounds=1,
        max_score_delta=1.0,
    )
    result = loop.run("seed output", {})

    # WITHOUT the fix, round 1's e1 threshold-met confirms round 2's e2 round as
    # "confirmed stable" and the loop stops at round 2. WITH the fix, round 2 is the
    # first threshold-met of epoch e2, so the loop must continue past round 2.
    assert result.total_rounds == 3
    assert [r.evaluator_epoch for r in result.rounds] == ["e1", "e2", "e2"]


def test_epoch_change_rebaselines_and_flags_stale() -> None:
    events: list[ImprovementLoopEvent] = []
    loop = ImprovementLoop(
        _EpochSwapTask(),
        max_rounds=2,
        quality_threshold=2.0,  # unreachable, force both rounds
        min_rounds=2,
        max_score_delta=0.1,  # a 0.9->0.4 cross-epoch drop would trip this if NOT re-baselined
        on_event=events.append,
    )
    result = loop.run("seed output", {})

    rebaseline = [e for e in events if e.event == "evaluator_epoch_rebaseline"]
    assert len(rebaseline) == 1
    assert rebaseline[0].stale_epoch == "e1"
    assert rebaseline[0].new_epoch == "e2"
    # after re-baseline, the loop's best reflects the new epoch (0.4), not the stale 0.9
    assert result.best_score == 0.4
    assert result.evaluator_epoch == "e2"
    # the round records carry their epochs
    assert [r.evaluator_epoch for r in result.rounds] == ["e1", "e2"]


def test_loop_dimension_pinning_does_not_rebaseline_or_discard_a_better_first_round() -> None:
    """Pinning the judge's own dimension names after round 1 (AC-48) changes the served
    specification, and so the epoch, but it is not an evaluator change: round 1 stays comparable."""
    events: list[ImprovementLoopEvent] = []
    provider = _ScriptedJudgeProvider([0.85, 0.60, 0.70], served_models=["judge-a"] * 3)
    task = SimpleAgentTask("write it", "Score 0-1 on accuracy.", provider, model="judge-a")

    result = ImprovementLoop(task, max_rounds=3, on_event=events.append).run("draft", {})

    served_pins = [JudgeServingSpec.model_validate_json(r.evaluator_spec).pinned_dimensions for r in result.rounds]
    assert served_pins == [(), ("accuracy",), ("accuracy",)]
    assert [e.round for e in events if e.event == EVALUATOR_EPOCH_REBASELINE] == []
    assert (result.best_round, result.best_score, result.best_output) == (1, 0.85, "draft")
    # lineage stays honest: the best score is attributed to the unpinned specification that served it
    assert result.evaluator_epoch == result.rounds[0].evaluator_epoch != result.rounds[1].evaluator_epoch


def test_evaluator_change_at_the_pinning_round_still_rebaselines() -> None:
    """The pinning exemption must not mask a real evaluator change that lands on the same round."""
    events: list[ImprovementLoopEvent] = []
    provider = _ScriptedJudgeProvider([0.85, 0.60, 0.70], served_models=["judge-a", "judge-b", "judge-b"])
    task = SimpleAgentTask("write it", "Score 0-1 on accuracy.", provider, model="judge-a")

    result = ImprovementLoop(task, max_rounds=3, on_event=events.append).run("draft", {})

    rebaselines = [e for e in events if e.event == EVALUATOR_EPOCH_REBASELINE]
    assert [(e.round, e.stale_epoch) for e in rebaselines] == [(2, result.rounds[0].evaluator_epoch)]
    assert (result.best_round, result.best_score) == (3, 0.70)


# Criteria deliberately declared out of sorted order.
_DECLARED_ORDER_RUBRIC = {
    "rubric_id": "declared-order",
    "goal": "Score the draft.",
    "criteria": [
        {"id": "clarity", "description": "Is it clear?", "scale_id": "unit"},
        {"id": "accuracy", "description": "Is it accurate?", "scale_id": "unit"},
    ],
    "scales": [{"id": "unit", "kind": "numeric"}],
}


class _DeclaredDimensionsTask:
    """A real LLMJudge on a typed rubric: it serves the declared dimension ids as pins from round 1."""

    def __init__(self, scores: list[float]) -> None:
        verdicts = iter(scores)

        def judge_llm(system: str, user: str) -> str:
            score = next(verdicts)
            return json.dumps({"score": score, "reasoning": "scripted", "dimensions": {"clarity": score, "accuracy": score}})

        self._judge = LLMJudge(model="judge-a", rubric=_DECLARED_ORDER_RUBRIC, llm_fn=judge_llm)
        self._revisions = 0

    def get_rubric(self) -> str:
        return self._judge.rubric

    def get_task_prompt(self, state: dict) -> str:
        return "write it"

    def evaluate_output(self, output, state, **kwargs) -> AgentTaskResult:
        verdict = self._judge.evaluate("write it", output, **kwargs)
        return AgentTaskResult(
            score=verdict.score,
            reasoning=verdict.reasoning,
            dimension_scores=verdict.dimension_scores,
            evaluator_epoch=verdict.evaluator_epoch,
            evaluator_spec=verdict.evaluator_spec,
        )

    def revise_output(self, output, feedback, state) -> str:
        self._revisions += 1
        return f"revision {self._revisions}"

    def verify_facts(self, output, state):
        return None


def test_loop_pins_declared_dimensions_in_their_served_order_and_keeps_one_epoch() -> None:
    """Re-pinning dimensions the judge was already served, in another order, would change the
    served specification for no evaluator reason."""
    events: list[ImprovementLoopEvent] = []

    result = ImprovementLoop(_DeclaredDimensionsTask([0.85, 0.60, 0.70]), max_rounds=3, on_event=events.append).run("draft", {})

    served_pins = [JudgeServingSpec.model_validate_json(r.evaluator_spec).pinned_dimensions for r in result.rounds]
    assert served_pins == [("clarity", "accuracy")] * 3
    assert len({r.evaluator_epoch for r in result.rounds}) == 1
    assert [e.round for e in events if e.event == EVALUATOR_EPOCH_REBASELINE] == []
    assert (result.best_round, result.best_score) == (1, 0.85)
