"""Tests for AC-733: external-verifier integration in ImprovementLoop.

Covers the case where the LLM judge says "perfect" but the external
verifier (e.g. a compiler) says "broken". The loop should:

1. Override the round's effective score to 0
2. Annotate the round's reasoning with the verifier's stderr/stdout
3. Treat the round as a non-passing round for threshold checks
4. Feed the verifier's message into the next revision prompt
"""

from __future__ import annotations

import sys
import textwrap

from autocontext.execution.improvement_loop import ImprovementLoop
from autocontext.execution.output_verifier import OutputVerifier
from autocontext.scenarios.agent_task import AgentTaskInterface, AgentTaskResult


class _AlwaysPerfectTask(AgentTaskInterface):
    """Task whose judge always returns 1.0 regardless of output content.

    Mirrors the real-world AC-733 failure mode: the LLM judge thinks the
    output is great even when a real verifier would reject it.
    """

    def __init__(self):
        self.revision_calls: list[str] = []

    def get_task_prompt(self, state: dict) -> str:
        return "Produce a clean Lean file."

    def evaluate_output(
        self,
        output,
        state,
        reference_context=None,
        required_concepts=None,
        calibration_examples=None,
        **kwargs,
    ):
        return AgentTaskResult(
            score=1.0,
            reasoning="judge: looks great",
            dimension_scores={"compiles": 1.0},
        )

    def revise_output(self, current_output, judge_result, state):
        # Capture the reasoning passed in so tests can assert verifier
        # feedback flows through to the next round.
        self.revision_calls.append(judge_result.reasoning)
        # Return a different output so the loop continues.
        return current_output + "\n-- revised"

    def get_rubric(self) -> str:
        return "Score 1.0 if perfect."

    def initial_state(self, seed=None) -> dict:
        return {}

    def describe_task(self):
        return "test"


class _UpgradingTask(AgentTaskInterface):
    """Like _AlwaysPerfectTask, but the second revision contains a "FIX"
    marker that a paired verifier can recognize as the passing version.

    Used to verify that the loop actually accepts the output once the
    verifier passes, rather than just permanently failing.
    """

    def get_task_prompt(self, state):
        return "."

    def evaluate_output(self, output, state, **kwargs):
        return AgentTaskResult(score=1.0, reasoning="judge: ok", dimension_scores={})

    def revise_output(self, current_output, judge_result, state):
        return current_output + "\nFIX"

    def get_rubric(self):
        return "."

    def initial_state(self, seed=None):
        return {}

    def describe_task(self):
        return "."


def _failing_verifier() -> OutputVerifier:
    """Verifier that always exits non-zero with a recognizable message."""
    script = textwrap.dedent(
        """
        import sys
        print('compile error: missing import on line 3', file=sys.stderr)
        sys.exit(2)
        """
    ).strip()
    return OutputVerifier(command=[sys.executable, "-c", script])


def _passing_verifier() -> OutputVerifier:
    """Verifier that always exits 0 with no error output."""
    return OutputVerifier(command=[sys.executable, "-c", "pass"])


def _picky_verifier() -> OutputVerifier:
    """Verifier that requires the input to contain 'FIX' (via stdin)."""
    script = textwrap.dedent(
        """
        import sys
        data = sys.stdin.read()
        if 'FIX' in data:
            sys.exit(0)
        print('missing FIX marker', file=sys.stderr)
        sys.exit(1)
        """
    ).strip()
    return OutputVerifier(command=[sys.executable, "-c", script])


# -- Core AC-733 behavior --


class TestVerifierIntegration:
    def test_failing_verifier_forces_score_to_zero(self):
        task = _AlwaysPerfectTask()
        loop = ImprovementLoop(
            task=task,
            max_rounds=2,
            quality_threshold=0.9,
            output_verifier=_failing_verifier(),
        )
        result = loop.run("initial output", {})

        # Judge returned 1.0 every round, but every round should record 0.0
        # because the verifier rejects the output.
        for r in result.rounds:
            assert r.score == 0.0, f"round {r.round_number} score should be 0 (verifier failed); got {r.score}"
        assert result.best_score == 0.0
        assert result.met_threshold is False
        # Loop must have run multiple rounds; threshold must not be met
        # despite the judge saying 1.0.
        assert result.total_rounds >= 1

    def test_failing_verifier_annotates_reasoning(self):
        task = _AlwaysPerfectTask()
        loop = ImprovementLoop(
            task=task,
            max_rounds=1,
            quality_threshold=0.9,
            output_verifier=_failing_verifier(),
        )
        result = loop.run("initial", {})
        round_one = result.rounds[0]
        assert "External Verifier Output" in round_one.reasoning
        assert "compile error" in round_one.reasoning

    def test_failing_verifier_feeds_back_into_revision(self):
        task = _AlwaysPerfectTask()
        loop = ImprovementLoop(
            task=task,
            max_rounds=2,
            quality_threshold=0.9,
            output_verifier=_failing_verifier(),
        )
        loop.run("initial", {})

        # The next revision call should have seen the verifier's error in
        # the judge_result.reasoning, so the agent can fix the actual issue.
        assert len(task.revision_calls) >= 1
        first_revision_reasoning = task.revision_calls[0]
        assert "compile error" in first_revision_reasoning

    def test_passing_verifier_does_not_change_score(self):
        task = _AlwaysPerfectTask()
        loop = ImprovementLoop(
            task=task,
            max_rounds=1,
            quality_threshold=0.9,
            output_verifier=_passing_verifier(),
        )
        result = loop.run("initial", {})
        assert result.best_score == 1.0
        assert result.met_threshold is True
        # Reasoning should NOT contain a verifier-failure block when the
        # verifier passed.
        assert "External Verifier Output" not in result.rounds[0].reasoning

    def test_no_verifier_means_no_change_to_existing_behavior(self):
        task = _AlwaysPerfectTask()
        loop = ImprovementLoop(
            task=task,
            max_rounds=1,
            quality_threshold=0.9,
            output_verifier=None,
        )
        result = loop.run("initial", {})
        assert result.best_score == 1.0
        assert result.met_threshold is True

    def test_verifier_starts_failing_then_passes_after_fix(self):
        # The verifier requires 'FIX' in the output. The task adds 'FIX' on
        # revision. After enough rounds we should see at least one passing
        # round and the threshold met.
        task = _UpgradingTask()
        loop = ImprovementLoop(
            task=task,
            max_rounds=3,
            quality_threshold=0.9,
            output_verifier=_picky_verifier(),
        )
        result = loop.run("seed", {})

        # At least one round should pass (where 'FIX' is present).
        passing_rounds = [r for r in result.rounds if r.score > 0]
        assert passing_rounds, "expected at least one passing round once 'FIX' was added"
        assert result.met_threshold is True

    def test_disabled_verifier_does_nothing(self):
        # A verifier with command=None should be silently ignored.
        task = _AlwaysPerfectTask()
        loop = ImprovementLoop(
            task=task,
            max_rounds=1,
            quality_threshold=0.9,
            output_verifier=OutputVerifier(command=None),
        )
        result = loop.run("initial", {})
        assert result.best_score == 1.0
        assert result.met_threshold is True
