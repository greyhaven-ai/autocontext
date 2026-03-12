"""Live e2e tests for agent self-improvement pipeline with real Anthropic API.

Skipped when ANTHROPIC_API_KEY is not set.
These tests make real API calls and cost real money — run intentionally.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from autocontext.execution.improvement_loop import ImprovementLoop
from autocontext.execution.judge import LLMJudge
from autocontext.execution.task_runner import SimpleAgentTask, TaskRunner, enqueue_task
from autocontext.notifications.callback import CallbackNotifier
from autocontext.providers.registry import create_provider
from autocontext.runtimes.direct_api import DirectAPIRuntime
from autocontext.storage.sqlite_store import SQLiteStore

pytestmark = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set",
)

MODEL = "claude-sonnet-4-20250514"
MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"


def _make_provider():
    return create_provider("anthropic", model=MODEL)


def _make_store():
    tmpdir = tempfile.mkdtemp()
    db_path = Path(tmpdir) / "live_test.db"
    store = SQLiteStore(db_path)
    store.migrate(MIGRATIONS_DIR)
    return store, Path(tmpdir)


class TestLiveAgentImprovement:
    """Live e2e tests using real Anthropic API calls."""

    def test_live_judge_evaluates_output(self):
        """Judge scores a good output higher than a bad output."""
        provider = _make_provider()
        judge = LLMJudge(
            provider=provider,
            model=MODEL,
            rubric="Evaluate whether the output is a valid haiku (5-7-5 syllable structure) about testing. Score 0-1.",
        )

        good_output = "Tests catch our errors\nSilent guards in the pipeline\nGreen lights bring us peace"
        bad_output = "testing is cool I guess maybe idk lol"

        good_result = judge.evaluate(
            task_prompt="Write a haiku about testing.",
            agent_output=good_output,
        )
        bad_result = judge.evaluate(
            task_prompt="Write a haiku about testing.",
            agent_output=bad_output,
        )

        print(f"\n  Good score: {good_result.score:.2f}, Bad score: {bad_result.score:.2f}")
        print(f"  Good reasoning: {good_result.reasoning[:100]}...")
        print(f"  Bad reasoning: {bad_result.reasoning[:100]}...")

        assert 0.0 <= good_result.score <= 1.0
        assert 0.0 <= bad_result.score <= 1.0
        assert good_result.score > bad_result.score, (
            f"Good ({good_result.score:.2f}) should score higher than bad ({bad_result.score:.2f})"
        )
        assert good_result.reasoning
        assert bad_result.reasoning

    def test_live_generate_and_judge(self):
        """Generate output via DirectAPIRuntime, then judge it."""
        provider = _make_provider()
        runtime = DirectAPIRuntime(provider=provider, model=MODEL)

        gen_output = runtime.generate(
            prompt="Write one sentence describing what a compiler does.",
            system="Be concise and accurate.",
        )
        assert len(gen_output.text) > 10

        judge = LLMJudge(
            provider=provider,
            model=MODEL,
            rubric="Accuracy and clarity of the description. Score 0-1.",
        )
        result = judge.evaluate(
            task_prompt="Write one sentence describing what a compiler does.",
            agent_output=gen_output.text,
        )

        print(f"\n  Generated: {gen_output.text[:120]}...")
        print(f"  Score: {result.score:.2f}")
        print(f"  Reasoning: {result.reasoning[:100]}...")

        assert result.score > 0.0
        assert result.reasoning

    def test_live_improvement_loop_2_rounds(self):
        """Run ImprovementLoop with 2 rounds, verify real scores."""
        provider = _make_provider()

        task = SimpleAgentTask(
            task_prompt="Write a haiku about recursion.",
            rubric="Valid haiku form (5-7-5 syllables), relevance to recursion, creativity. Score 0-1.",
            provider=provider,
            model=MODEL,
        )

        loop = ImprovementLoop(task=task, max_rounds=2, quality_threshold=0.95)
        result = loop.run(
            initial_output="Code calls itself deep\nStack frames pile up like dreams\nBase case sets us free",
            state={},
        )

        print(f"\n  Total rounds: {result.total_rounds}")
        print(f"  Best score: {result.best_score:.2f} (round {result.best_round})")
        for r in result.rounds:
            print(f"    Round {r.round_number}: score={r.score:.2f}, failed={r.judge_failed}")
            print(f"      Reasoning: {r.reasoning[:80]}...")

        assert result.total_rounds >= 1
        assert result.total_rounds <= 2
        for r in result.rounds:
            if not r.judge_failed:
                assert 0.0 <= r.score <= 1.0
                assert len(r.reasoning) > 10, "Reasoning should be substantive"

    def test_live_full_pipeline_with_task_runner(self):
        """Enqueue a task, run TaskRunner.run_once(), verify completion."""
        store, tmpdir = _make_store()
        provider = _make_provider()

        enqueue_task(
            store,
            spec_name="live-pipeline-test",
            task_prompt="Write one sentence about why code review matters.",
            rubric="Insight, clarity, brevity. Score 0-1.",
            quality_threshold=0.95,
            max_rounds=2,
        )

        assert store.pending_task_count() == 1

        events = []
        notifier = CallbackNotifier(events.append)
        runner = TaskRunner(
            store=store, provider=provider, model=MODEL, notifier=notifier,
        )
        completed = runner.run_once()

        print(f"\n  Status: {completed['status']}")
        print(f"  Best score: {completed['best_score']}")
        print(f"  Rounds: {completed['total_rounds']}")
        if completed.get("best_output"):
            print(f"  Best output: {completed['best_output'][:120]}...")
        if events:
            print(f"  Event: {events[0].type.value}, score={events[0].score}")

        assert completed is not None
        assert completed["status"] == "completed"
        assert completed["best_score"] > 0
        assert completed["best_output"]
        assert store.pending_task_count() == 0

    def test_live_human_feedback_round_trip(self):
        """Record human feedback, use it as calibration examples in judge call."""
        store, tmpdir = _make_store()
        provider = _make_provider()
        scenario = "live-calibration-test"

        store.insert_human_feedback(
            scenario_name=scenario,
            agent_output="Testing helps find bugs before users do.",
            human_score=0.8,
            human_notes="Good insight, could be more specific.",
        )
        store.insert_human_feedback(
            scenario_name=scenario,
            agent_output="Tests are good.",
            human_score=0.2,
            human_notes="Too vague, no substance.",
        )

        calibration = store.get_calibration_examples(scenario, limit=5)
        assert len(calibration) == 2

        cal_examples = [
            {
                "agent_output": ex["agent_output"],
                "human_score": ex["human_score"],
                "human_notes": ex["human_notes"],
            }
            for ex in calibration
        ]

        judge = LLMJudge(
            provider=provider,
            model=MODEL,
            rubric="Clarity and insight about software testing. Score 0-1.",
        )

        result = judge.evaluate(
            task_prompt="Write one sentence about the value of testing.",
            agent_output="Automated tests act as a safety net, catching regressions before they reach production.",
            calibration_examples=cal_examples,
        )

        print(f"\n  Score with calibration: {result.score:.2f}")
        print(f"  Reasoning: {result.reasoning[:100]}...")

        assert 0.0 <= result.score <= 1.0
        assert result.reasoning
