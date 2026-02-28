"""End-to-end smoke test — exercises the full MTS Phase A stack with a real provider.

Tests:
1. AnthropicProvider.complete() — real API call
2. LLMJudge.evaluate() — real scoring with structured output parsing
3. TaskRunner.run_once() — queue → dequeue → generate → judge → store result
4. Notifications — CallbackNotifier fires on completion
5. DirectAPIRuntime — generate + revise
"""

import json
import sys
import tempfile
from pathlib import Path

# Ensure we import from this repo
sys.path.insert(0, str(Path(__file__).parent / "src"))

from mts.providers.anthropic import AnthropicProvider
from mts.providers.registry import create_provider
from mts.execution.judge import LLMJudge
from mts.execution.task_runner import TaskRunner, enqueue_task
from mts.storage.sqlite_store import SQLiteStore
from mts.notifications.callback import CallbackNotifier
from mts.notifications.base import EventType
from mts.runtimes.direct_api import DirectAPIRuntime


def section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def main():
    results = {}

    # ─── 1. Provider: real API call ───────────────────────────
    section("1. AnthropicProvider — real API call")
    provider = create_provider("anthropic", model="claude-sonnet-4-20250514")
    result = provider.complete(
        system_prompt="You are a helpful assistant. Reply in exactly one sentence.",
        user_prompt="What is MTS (Monitoring The Situation)?",
    )
    print(f"  Model: {result.model}")
    print(f"  Response: {result.text[:150]}...")
    assert len(result.text) > 10, "Response too short"
    results["provider"] = "✅ PASS"

    # ─── 2. LLMJudge: real scoring ───────────────────────────
    section("2. LLMJudge — real evaluation")
    judge = LLMJudge(
        provider=provider,
        model="claude-sonnet-4-20250514",
        rubric="Score based on: factual accuracy (is the description correct?), clarity (easy to understand?), completeness (covers key aspects?). Score 0-1.",
    )
    eval_result = judge.evaluate(
        task_prompt="Write a one-paragraph explanation of recursive language models.",
        agent_output="Recursive Language Models (RLMs) are a class of language models that iteratively refine their own outputs through multiple passes, using each previous generation as input for the next. Unlike standard autoregressive models that generate text in a single left-to-right pass, RLMs apply a recursive loop where the model critiques, revises, and improves its output over several rounds. This approach, introduced by Alex Zhang in October 2025, enables the model to self-correct errors, deepen reasoning, and produce higher-quality outputs without requiring external feedback.",
    )
    print(f"  Score: {eval_result.score}")
    print(f"  Reasoning: {eval_result.reasoning[:150]}...")
    print(f"  Dimensions: {json.dumps(eval_result.dimension_scores, indent=2)[:200]}")
    assert 0.0 <= eval_result.score <= 1.0, f"Score out of range: {eval_result.score}"
    assert eval_result.reasoning, "No reasoning returned"
    results["judge"] = f"✅ PASS (score: {eval_result.score:.2f})"

    # ─── 3. DirectAPIRuntime: generate + revise ──────────────
    section("3. DirectAPIRuntime — generate + revise")
    runtime = DirectAPIRuntime(provider, model="claude-sonnet-4-20250514")
    gen_output = runtime.generate(
        "Write a two-sentence description of an AI evaluation harness.",
        system="Be concise and technical.",
    )
    print(f"  Generated: {gen_output.text[:150]}...")
    rev_output = runtime.revise(
        prompt="Write a two-sentence description of an AI evaluation harness.",
        previous_output=gen_output.text,
        feedback="Make it more specific — mention scoring rubrics and improvement loops.",
    )
    print(f"  Revised: {rev_output.text[:150]}...")
    assert len(rev_output.text) > 20, "Revision too short"
    results["runtime"] = "✅ PASS"

    # ─── 4. TaskRunner + Notifications: full pipeline ────────
    section("4. TaskRunner + Notifications — full pipeline")
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "smoke.db"
        store = SQLiteStore(db_path)
        migrations_dir = Path(__file__).parent / "migrations"
        store.migrate(migrations_dir)

        events = []
        notifier = CallbackNotifier(events.append)

        enqueue_task(
            store, "smoke-test",
            task_prompt="Write a single sentence about why AI evaluation matters.",
            rubric="Clarity, insight, brevity. Score 0-1.",
            quality_threshold=0.5,
            max_rounds=2,
        )

        # Verify task was queued
        count = store.pending_task_count()
        print(f"  Queued tasks: {count}")
        assert count == 1, f"Expected 1 queued task, got {count}"

        runner = TaskRunner(store=store, provider=provider, notifier=notifier)
        runner.run_once()

        print(f"  Notifications received: {len(events)}")
        if events:
            e = events[0]
            print(f"  Event type: {e.type.value}")
            print(f"  Task: {e.task_name}")
            print(f"  Score: {e.score}")
            print(f"  Summary: {e.summary[:100]}")

        # Check the result was stored
        remaining = store.pending_task_count()
        print(f"  Remaining tasks: {remaining}")
        assert remaining == 0, f"Task not processed, {remaining} remaining"
        assert len(events) >= 1, "No notification fired"
        results["pipeline"] = f"✅ PASS (event: {events[0].type.value}, score: {events[0].score})"

    # ─── Summary ─────────────────────────────────────────────
    section("SMOKE TEST RESULTS")
    all_pass = True
    for name, status in results.items():
        print(f"  {name}: {status}")
        if "FAIL" in status:
            all_pass = False

    if all_pass:
        print(f"\n  🟢 ALL {len(results)} TESTS PASSED")
    else:
        print(f"\n  🔴 SOME TESTS FAILED")
        sys.exit(1)


if __name__ == "__main__":
    main()
