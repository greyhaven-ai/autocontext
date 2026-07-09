from __future__ import annotations

from autocontext.execution.improvement_events import ImprovementLoopEvent
from autocontext.execution.improvement_loop import ImprovementLoop
from autocontext.scenarios.agent_task import AgentTaskResult


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
