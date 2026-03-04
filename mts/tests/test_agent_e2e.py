"""End-to-end tests for the agent self-improvement pipeline.

Uses mocked LLM providers (deterministic, no real API calls) so tests run in CI.
Covers: task creation → generation → judging → improvement loop → feedback → export.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest

from mts.execution.improvement_loop import ImprovementLoop, ImprovementResult
from mts.execution.judge import LLMJudge
from mts.execution.task_runner import SimpleAgentTask, TaskRunner, enqueue_task
from mts.notifications.base import EventType, NotificationEvent
from mts.notifications.callback import CallbackNotifier
from mts.providers.callable_wrapper import CallableProvider
from mts.runtimes.direct_api import DirectAPIRuntime
from mts.scenarios.agent_task import AgentTaskResult
from mts.storage.sqlite_store import SQLiteStore

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def make_judge_response(score: float) -> str:
    """Build a structured judge response with JUDGE_RESULT markers."""
    return (
        "Here is my evaluation.\n"
        "<!-- JUDGE_RESULT_START -->\n"
        f'{{"score": {score}, "reasoning": "Evaluation reasoning for score {score:.2f}", '
        f'"dimensions": {{"accuracy": {score}, "clarity": {score}}}}}\n'
        "<!-- JUDGE_RESULT_END -->"
    )


class _CallCounter:
    """Thread-safe-ish call counter for deterministic mock sequences."""

    def __init__(self) -> None:
        self.count = 0

    def next(self) -> int:
        val = self.count
        self.count += 1
        return val


def _make_mock_fn(
    judge_scores: list[float] | None = None,
    generation_text: str = "Generated output about the topic.",
    revision_text: str = "Improved output incorporating feedback.",
) -> tuple[CallableProvider, _CallCounter]:
    """Create a CallableProvider that returns deterministic responses.

    Judge calls (detected by 'JUDGE_RESULT' or 'rubric' in prompts) return
    structured judge responses with increasing scores.  All other calls
    return generation or revision text.
    """
    counter = _CallCounter()
    scores = list(judge_scores or [0.4, 0.6, 0.85])
    score_idx = _CallCounter()

    def mock_fn(system: str, user: str) -> str:
        call_num = counter.next()
        # Detect judge calls
        lower = (system + user).lower()
        if "judge" in lower or "rubric" in lower or "evaluate" in lower or "JUDGE_RESULT" in (system + user):
            idx = min(score_idx.next(), len(scores) - 1)
            return make_judge_response(scores[idx])
        # Detect revision calls
        if "revis" in lower or "improv" in lower or "feedback" in lower:
            return f"{revision_text} (call {call_num})"
        return f"{generation_text} (call {call_num})"

    provider = CallableProvider(mock_fn, model_name="mock-model")
    return provider, counter


def _make_store() -> tuple[SQLiteStore, Path]:
    """Create a temp SQLiteStore with migrations applied."""
    tmpdir = tempfile.mkdtemp()
    db_path = Path(tmpdir) / "test.db"
    store = SQLiteStore(db_path)
    store.migrate(MIGRATIONS_DIR)
    return store, Path(tmpdir)


# ===========================================================================
# Class 1: TestAgentSelfImprovementE2E
# ===========================================================================


class TestAgentSelfImprovementE2E:
    """Full pipeline with deterministic mock provider."""

    def test_create_task_and_evaluate(self) -> None:
        """Create an agent task spec, generate output, judge scores it."""
        provider, _ = _make_mock_fn(judge_scores=[0.72])

        task = SimpleAgentTask(
            task_prompt="Write a summary of quantum computing.",
            rubric="Accuracy, clarity, completeness. Score 0-1.",
            provider=provider,
            model="mock-model",
        )

        output = task.generate_output({})
        assert len(output) > 0, "generate_output should produce text"

        result = task.evaluate_output(output, state={})
        assert isinstance(result, AgentTaskResult)
        assert 0.0 <= result.score <= 1.0
        assert result.score == pytest.approx(0.72, abs=0.01)
        assert result.reasoning, "Should have reasoning"

    def test_improvement_loop_improves_score(self) -> None:
        """Run ImprovementLoop, verify score improves across rounds."""
        provider, _ = _make_mock_fn(judge_scores=[0.4, 0.6, 0.85])

        task = SimpleAgentTask(
            task_prompt="Explain machine learning in simple terms.",
            rubric="Clarity and accuracy. Score 0-1.",
            provider=provider,
            model="mock-model",
        )

        loop = ImprovementLoop(task=task, max_rounds=3, quality_threshold=0.9)
        result = loop.run(initial_output="Initial ML explanation.", state={})

        assert isinstance(result, ImprovementResult)
        assert len(result.rounds) >= 2, "Should have multiple rounds"
        assert result.best_score >= 0.85, f"Best score should be >= 0.85, got {result.best_score}"
        assert result.improved, "Score should have improved"

        # Verify scores are monotonically increasing across valid rounds
        valid_scores = [r.score for r in result.rounds if not r.judge_failed]
        assert valid_scores == sorted(valid_scores), f"Scores should increase: {valid_scores}"

    def test_full_pipeline_create_to_export(self) -> None:
        """Full flow: create task → enqueue → TaskRunner.run_once() → verify completed + score stored."""
        store, tmpdir = _make_store()
        provider, _ = _make_mock_fn(judge_scores=[0.5, 0.75, 0.92])

        enqueue_task(
            store,
            spec_name="test-pipeline",
            task_prompt="Write about neural networks.",
            rubric="Technical accuracy and clarity. Score 0-1.",
            quality_threshold=0.9,
            max_rounds=3,
        )

        assert store.pending_task_count() == 1

        runner = TaskRunner(store=store, provider=provider, model="mock-model")
        completed = runner.run_once()

        assert completed is not None, "run_once should return a task"
        assert completed["status"] == "completed"
        assert completed["best_score"] is not None
        assert completed["best_score"] >= 0.5
        assert completed["total_rounds"] >= 1
        assert store.pending_task_count() == 0

    def test_human_feedback_calibrates_future_runs(self) -> None:
        """Record human feedback via SQLiteStore, verify it's retrievable for calibration."""
        store, tmpdir = _make_store()
        scenario = "test-calibration"

        # Record human feedback
        store.insert_human_feedback(
            scenario_name=scenario,
            agent_output="This is a good explanation of transformers.",
            human_score=0.85,
            human_notes="Good coverage but missing attention mechanism details.",
        )
        store.insert_human_feedback(
            scenario_name=scenario,
            agent_output="Transformers use self-attention.",
            human_score=0.4,
            human_notes="Too brief, lacks depth.",
        )

        # Retrieve calibration examples
        calibration = store.get_calibration_examples(scenario, limit=5)
        assert len(calibration) == 2
        assert calibration[0]["human_score"] == 0.85 or calibration[1]["human_score"] == 0.85

        # Verify calibration examples can be passed to judge
        provider, _ = _make_mock_fn(judge_scores=[0.7])
        judge = LLMJudge(
            model="mock-model",
            rubric="Quality evaluation.",
            provider=provider,
        )

        cal_examples = [
            {
                "agent_output": ex["agent_output"],
                "human_score": ex["human_score"],
                "human_notes": ex["human_notes"],
            }
            for ex in calibration
        ]

        result = judge.evaluate(
            task_prompt="Explain transformers.",
            agent_output="Test output.",
            calibration_examples=cal_examples,
        )
        assert result.score == pytest.approx(0.7, abs=0.01)

    def test_task_runner_with_notifications(self) -> None:
        """Enqueue task, run with CallbackNotifier, verify correct events fire."""
        store, tmpdir = _make_store()
        provider, _ = _make_mock_fn(judge_scores=[0.95])  # Meets threshold immediately
        events: list[NotificationEvent] = []
        notifier = CallbackNotifier(events.append)

        enqueue_task(
            store,
            spec_name="notify-test",
            task_prompt="Write a haiku about AI.",
            rubric="Creativity and form. Score 0-1.",
            quality_threshold=0.9,
            max_rounds=3,
        )

        runner = TaskRunner(
            store=store, provider=provider, model="mock-model", notifier=notifier
        )
        runner.run_once()

        assert len(events) >= 1, "Should have at least one notification"
        event = events[0]
        assert event.task_name == "notify-test"
        assert event.type in (EventType.THRESHOLD_MET, EventType.COMPLETION)
        assert event.score is not None and event.score > 0


# ===========================================================================
# Class 2: TestMCPToolsAgentFlow
# ===========================================================================


class TestMCPToolsAgentFlow:
    """Tests using MCP tool functions directly (simulating agent MCP calls).

    These tests use the lower-level MtsToolContext and MCP tool functions
    to exercise the flow an agent would use through MCP.
    """

    def _make_ctx(self) -> tuple[Any, Path]:
        """Create an MtsToolContext with temp dirs."""
        from mts.config import AppSettings
        from mts.mcp.tools import MtsToolContext

        tmpdir = Path(tempfile.mkdtemp())
        settings = AppSettings(
            db_path=tmpdir / "test.db",
            runs_root=tmpdir / "runs",
            knowledge_root=tmpdir / "knowledge",
            skills_root=tmpdir / "skills",
            claude_skills_path=tmpdir / ".claude" / "skills",
        )
        ctx = MtsToolContext(settings)
        ctx.sqlite.migrate(MIGRATIONS_DIR)
        return ctx, tmpdir

    def test_mcp_create_evaluate_flow(self) -> None:
        """create_agent_task() → evaluate_output() flow using mocked provider."""
        from mts.mcp.tools import create_agent_task

        ctx, tmpdir = self._make_ctx()

        # Create task
        result = create_agent_task(
            ctx,
            name="test-eval-task",
            task_prompt="Summarize the benefits of test-driven development.",
            rubric="Completeness, accuracy, conciseness. Score 0-1.",
        )
        assert result["status"] == "created"
        assert result["name"] == "test-eval-task"

        # Evaluate — we need to monkey-patch the provider registry for this test
        # Since evaluate_output uses get_provider internally, we test the lower
        # level flow instead: read the spec and call LLMJudge directly
        spec_path = ctx.settings.knowledge_root / "_agent_tasks" / "test-eval-task.json"
        assert spec_path.exists()

        data = json.loads(spec_path.read_text())
        provider, _ = _make_mock_fn(judge_scores=[0.78])
        judge = LLMJudge(
            model="mock-model",
            rubric=data["rubric"],
            provider=provider,
        )
        judge_result = judge.evaluate(
            task_prompt=data["task_prompt"],
            agent_output="TDD helps catch bugs early and improves design.",
        )
        assert judge_result.score == pytest.approx(0.78, abs=0.01)

    def test_mcp_improvement_loop(self) -> None:
        """Create task → run improvement loop via MCP-style flow."""
        from mts.mcp.tools import create_agent_task

        ctx, tmpdir = self._make_ctx()

        create_agent_task(
            ctx,
            name="test-loop-task",
            task_prompt="Explain containerization.",
            rubric="Technical depth and clarity. Score 0-1.",
            max_rounds=3,
            quality_threshold=0.9,
        )

        # Build a SimpleAgentTask and run ImprovementLoop (same as MCP tool does internally)
        provider, _ = _make_mock_fn(judge_scores=[0.3, 0.6, 0.88])
        task = SimpleAgentTask(
            task_prompt="Explain containerization.",
            rubric="Technical depth and clarity. Score 0-1.",
            provider=provider,
            model="mock-model",
        )

        loop = ImprovementLoop(task=task, max_rounds=3, quality_threshold=0.9)
        result = loop.run(
            initial_output="Containers package apps.",
            state={},
        )

        assert result.best_score >= 0.88
        assert result.improved
        assert result.total_rounds >= 2

    def test_mcp_queue_and_process(self) -> None:
        """queue_improvement_run() → TaskRunner.run_once() → get result."""
        from mts.mcp.tools import create_agent_task

        ctx, tmpdir = self._make_ctx()

        create_agent_task(
            ctx,
            name="queue-test",
            task_prompt="Write about microservices.",
            rubric="Architecture knowledge. Score 0-1.",
            max_rounds=2,
            quality_threshold=0.8,
        )

        # Enqueue via the store (same as queue_improvement_run does)
        task_id = enqueue_task(
            ctx.sqlite,
            spec_name="queue-test",
            task_prompt="Write about microservices.",
            rubric="Architecture knowledge. Score 0-1.",
            max_rounds=2,
            quality_threshold=0.8,
        )

        assert ctx.sqlite.pending_task_count() == 1

        provider, _ = _make_mock_fn(judge_scores=[0.65, 0.82])
        runner = TaskRunner(store=ctx.sqlite, provider=provider, model="mock-model")
        completed = runner.run_once()

        assert completed is not None
        assert completed["status"] == "completed"
        assert completed["best_score"] >= 0.65

        # Verify via get_task
        task_data = ctx.sqlite.get_task(task_id)
        assert task_data is not None
        assert task_data["status"] == "completed"

    def test_mcp_list_and_get_tasks(self) -> None:
        """CRUD operations on agent tasks via MCP tool functions."""
        from mts.mcp.tools import (
            create_agent_task,
            delete_agent_task,
            get_agent_task,
            list_agent_tasks,
        )

        ctx, tmpdir = self._make_ctx()

        # Create multiple tasks
        create_agent_task(ctx, name="task-alpha", task_prompt="Alpha prompt.", rubric="Alpha rubric.")
        create_agent_task(ctx, name="task-beta", task_prompt="Beta prompt.", rubric="Beta rubric.")
        create_agent_task(ctx, name="task-gamma", task_prompt="Gamma prompt.", rubric="Gamma rubric.")

        # List
        tasks = list_agent_tasks(ctx)
        assert len(tasks) == 3
        names = {t["name"] for t in tasks}
        assert names == {"task-alpha", "task-beta", "task-gamma"}

        # Get
        alpha = get_agent_task(ctx, "task-alpha")
        assert alpha["task_prompt"] == "Alpha prompt."
        assert alpha["rubric"] == "Alpha rubric."

        # Delete
        result = delete_agent_task(ctx, "task-beta")
        assert result["status"] == "deleted"

        tasks = list_agent_tasks(ctx)
        assert len(tasks) == 2

        # Get non-existent
        missing = get_agent_task(ctx, "task-beta")
        assert "error" in missing


# ===========================================================================
# Class 3: TestAgentRuntimeE2E
# ===========================================================================


class TestAgentRuntimeE2E:
    """Tests using DirectAPIRuntime."""

    def test_generate_and_revise_with_feedback(self) -> None:
        """Runtime generates, then revises based on judge result."""
        provider, counter = _make_mock_fn(
            judge_scores=[0.5],
            generation_text="Initial explanation of attention mechanisms.",
            revision_text="Improved explanation with self-attention details.",
        )

        runtime = DirectAPIRuntime(provider=provider, model="mock-model")

        gen_output = runtime.generate(
            prompt="Explain attention mechanisms in transformers.",
            system="Be technical and precise.",
        )
        assert len(gen_output.text) > 0
        assert "Initial explanation" in gen_output.text or "call" in gen_output.text

        # Judge the output
        judge = LLMJudge(
            model="mock-model",
            rubric="Technical accuracy. Score 0-1.",
            provider=provider,
        )
        judge_result = judge.evaluate(
            task_prompt="Explain attention mechanisms.",
            agent_output=gen_output.text,
        )
        assert judge_result.score == pytest.approx(0.5, abs=0.01)

        # Revise based on feedback
        rev_output = runtime.revise(
            prompt="Explain attention mechanisms in transformers.",
            previous_output=gen_output.text,
            feedback=judge_result.reasoning,
        )
        assert len(rev_output.text) > 0
        # Revision call should be different from generation
        assert rev_output.text != gen_output.text

    def test_runtime_feeds_into_improvement_loop(self) -> None:
        """Runtime output → ImprovementLoop → improved result."""
        provider, _ = _make_mock_fn(judge_scores=[0.35, 0.6, 0.9])

        runtime = DirectAPIRuntime(provider=provider, model="mock-model")

        # Generate initial output via runtime
        gen_output = runtime.generate(
            prompt="Write a technical overview of graph neural networks.",
        )
        assert len(gen_output.text) > 0

        # Feed into improvement loop
        task = SimpleAgentTask(
            task_prompt="Write a technical overview of graph neural networks.",
            rubric="Depth, accuracy, structure. Score 0-1.",
            provider=provider,
            model="mock-model",
        )

        loop = ImprovementLoop(task=task, max_rounds=3, quality_threshold=0.85)
        result = loop.run(initial_output=gen_output.text, state={})

        assert result.best_score >= 0.9
        assert result.met_threshold, "Should meet 0.85 threshold with score 0.9"
        assert result.improved
        assert len(result.rounds) >= 2
