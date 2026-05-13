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

    def test_fenced_seed_is_stripped_before_verifier_sees_it(self):
        # AC-754: a markdown-fenced seed must be unwrapped before round 1's
        # verifier runs, so the verifier never sees the literal ```lang lines.
        # A picky verifier that fails on any line starting with ``` would
        # otherwise reject the round; with the fence strip, it passes.
        script = textwrap.dedent(
            """
            import sys
            data = sys.stdin.read()
            for line in data.splitlines():
                if line.lstrip().startswith('```'):
                    print('found unexpected fence: ' + line, file=sys.stderr)
                    sys.exit(2)
            sys.exit(0)
            """
        ).strip()
        no_fence_verifier = OutputVerifier(command=[sys.executable, "-c", script])

        task = _AlwaysPerfectTask()
        loop = ImprovementLoop(
            task=task,
            max_rounds=1,
            quality_threshold=0.9,
            output_verifier=no_fence_verifier,
        )
        fenced_seed = "```lean\ntheorem foo : 1 = 1 := rfl\n```"
        result = loop.run(fenced_seed, {})

        # Verifier passed -> effective score not zeroed -> best_score == 1.0.
        assert result.best_score == 1.0
        assert result.met_threshold is True
        # And the stored output should be the unwrapped form.
        assert result.best_output == "theorem foo : 1 = 1 := rfl"


# -- AC-750: max_score_delta warning vs verifier-veto provenance --


class _StagedJudgeTask(AgentTaskInterface):
    """Task whose judge returns a different score depending on whether the
    output has been revised (i.e. contains the 'FIX' marker).

    This pairs with `_picky_verifier` so we can simulate:
      round 1: judge=initial_score, verifier vetoes (no FIX yet) -> effective 0
      round 2: judge=revised_score,  verifier passes (FIX added) -> effective revised_score
    """

    def __init__(self, *, initial_score: float, revised_score: float) -> None:
        self._initial_score = initial_score
        self._revised_score = revised_score

    def get_task_prompt(self, state):
        return "Produce a clean Lean file."

    def evaluate_output(self, output, state, **kwargs):
        if "FIX" in output:
            return AgentTaskResult(
                score=self._revised_score,
                reasoning="judge: revised output looks good",
                dimension_scores={},
            )
        return AgentTaskResult(
            score=self._initial_score,
            reasoning="judge: initial output is weak",
            dimension_scores={},
        )

    def revise_output(self, current_output, judge_result, state):
        return current_output + "\nFIX"

    def get_rubric(self):
        return "Score 0-1."

    def initial_state(self, seed=None):
        return {}

    def describe_task(self):
        return "."


class TestVerifierVetoProvenance:
    """When the external verifier vetoes a round, `prev_valid_score` becomes 0
    -- but that 0 is NOT a real judge baseline. The next round's legitimate
    judge score (e.g. 0.6) should not trigger a misleading
    `max_score_delta` warning against the veto-zeroed 0.0.

    Concrete repro (2026-05-11): a 3-round Opus run against `lake env lean`.
    Round 1 timed out -> verifier vetoed -> score 0. Round 2's judge honestly
    scored 0.6 (the model had fixed several issues) but the warning fired:
        `Score jump of 0.600 exceeds max_score_delta 0.500 (round 2: 0.000 -> 0.600)`
    The warning is misleading; round 1's 0 was a veto, not a judge score.
    """

    def test_no_warning_when_previous_round_was_verifier_vetoed(self, caplog) -> None:
        import logging

        # Round 1: judge=0.4, verifier vetoes (no FIX) -> effective 0
        # Round 2: judge=0.6, verifier passes (FIX added) -> effective 0.6
        # Under the buggy logic this fires "score jump 0.600 vs 0.000".
        # After the fix the warning is suppressed because round 1's 0 was a veto.
        task = _StagedJudgeTask(initial_score=0.4, revised_score=0.6)
        loop = ImprovementLoop(
            task=task,
            max_rounds=2,
            quality_threshold=0.9,
            max_score_delta=0.5,
            output_verifier=_picky_verifier(),
        )

        with caplog.at_level(logging.WARNING, logger="autocontext.execution.improvement_loop"):
            loop.run("initial", {})

        score_jump_warnings = [record for record in caplog.records if "Score jump" in record.getMessage()]
        assert score_jump_warnings == [], (
            "verifier-vetoed previous round should not be a baseline for the "
            f"max_score_delta warning; got: {[r.getMessage() for r in score_jump_warnings]}"
        )

    def test_warning_still_fires_for_genuine_judge_score_jump(self, caplog) -> None:
        import logging

        # No verifier. Round 1 judge=0.1, round 2 judge=0.7. Genuine jump
        # exceeding max_score_delta=0.5 -- warning should still fire because
        # the previous score is a real judge baseline.
        task = _StagedJudgeTask(initial_score=0.1, revised_score=0.7)
        loop = ImprovementLoop(
            task=task,
            max_rounds=2,
            quality_threshold=0.9,
            max_score_delta=0.5,
        )

        with caplog.at_level(logging.WARNING, logger="autocontext.execution.improvement_loop"):
            loop.run("initial", {})

        score_jump_warnings = [record for record in caplog.records if "Score jump" in record.getMessage()]
        assert score_jump_warnings, (
            "genuine score jump (no verifier veto on previous round) should still trigger the max_score_delta warning"
        )


# -- AC-727 slice: per-round checkpoint command --


class _RecordingTask(AgentTaskInterface):
    """Two-round task. Each round produces a slightly different output so
    we can assert the checkpoint command sees each one."""

    def get_task_prompt(self, state):
        return "."

    def evaluate_output(self, output, state, **kwargs):
        # Sub-threshold for both rounds so the loop runs to max_rounds.
        return AgentTaskResult(score=0.5, reasoning="x", dimension_scores={})

    def revise_output(self, current_output, judge_result, state):
        return current_output + "-rev"

    def get_rubric(self):
        return "."

    def initial_state(self, seed=None):
        return {}

    def describe_task(self):
        return "."


def _capturing_checkpoint(tmp_path):
    """Build a checkpoint command that appends the per-round output to a
    capture file, plus the path so the test can read it back. The command
    uses `{file}` so we exercise the file-mode placeholder path.

    Returns (command_template, captured_outputs_callable).
    """
    capture = tmp_path / "checkpoint-captures.log"
    capture.write_text("")
    script = textwrap.dedent(
        f"""
        import sys
        from pathlib import Path
        src = Path(sys.argv[1])
        Path({str(capture)!r}).open("a", encoding="utf-8").write(src.read_text() + "\\n---\\n")
        sys.exit(0)
        """
    ).strip()
    return ([sys.executable, "-c", script, "{file}"], capture)


def _failing_checkpoint():
    """Checkpoint command that always exits non-zero."""
    script = textwrap.dedent(
        """
        import sys
        print('checkpoint script blew up', file=sys.stderr)
        sys.exit(7)
        """
    ).strip()
    return OutputVerifier(command=[sys.executable, "-c", script])


class TestCheckpointer:
    """AC-727: a per-round checkpoint command preserves partial progress
    before later rounds overshoot. Unlike `--verify-cmd`, a checkpoint
    failure must NOT veto the round."""

    def test_checkpointer_invoked_each_round_with_output(self, tmp_path) -> None:
        command, capture = _capturing_checkpoint(tmp_path)
        checkpointer = OutputVerifier(command=command, file_suffix=".txt")

        loop = ImprovementLoop(
            task=_RecordingTask(),
            max_rounds=2,
            quality_threshold=0.9,
            output_checkpointer=checkpointer,
        )
        loop.run("seed", {})

        snapshots = [s for s in capture.read_text().split("\n---\n") if s]
        assert snapshots == ["seed", "seed-rev"], f"checkpointer did not capture per-round output; got {snapshots!r}"

    def test_checkpointer_failure_does_not_abort_loop(self) -> None:
        # Failing checkpointer must not veto the run. The loop continues to
        # max_rounds and the result reflects the judge's view, unchanged.
        loop = ImprovementLoop(
            task=_RecordingTask(),
            max_rounds=2,
            quality_threshold=0.9,
            output_checkpointer=_failing_checkpoint(),
        )
        result = loop.run("seed", {})
        assert result.total_rounds == 2
        assert result.best_score == 0.5  # judge score, not 0; no veto

    def test_checkpoint_done_event_emitted(self, tmp_path) -> None:
        from autocontext.execution.improvement_events import ImprovementLoopEvent

        events: list[ImprovementLoopEvent] = []
        command, _capture = _capturing_checkpoint(tmp_path)
        checkpointer = OutputVerifier(command=command, file_suffix=".txt")

        loop = ImprovementLoop(
            task=_RecordingTask(),
            max_rounds=1,
            quality_threshold=0.9,
            output_checkpointer=checkpointer,
            on_event=events.append,
        )
        loop.run("seed", {})

        checkpoint_events = [e for e in events if e.event == "checkpoint_done"]
        assert len(checkpoint_events) == 1
        ev = checkpoint_events[0]
        assert ev.round == 1
        assert ev.checkpoint_ok is True
        assert ev.checkpoint_exit_code == 0

    def test_checkpoint_done_event_records_failure(self) -> None:
        from autocontext.execution.improvement_events import ImprovementLoopEvent

        events: list[ImprovementLoopEvent] = []
        loop = ImprovementLoop(
            task=_RecordingTask(),
            max_rounds=1,
            quality_threshold=0.9,
            output_checkpointer=_failing_checkpoint(),
            on_event=events.append,
        )
        loop.run("seed", {})

        checkpoint_events = [e for e in events if e.event == "checkpoint_done"]
        assert len(checkpoint_events) == 1
        ev = checkpoint_events[0]
        assert ev.checkpoint_ok is False
        assert ev.checkpoint_exit_code == 7
