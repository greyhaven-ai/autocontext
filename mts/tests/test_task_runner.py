"""Tests for the task runner daemon and task queue."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mts.execution.improvement_loop import ImprovementResult, RoundResult
from mts.execution.task_runner import (
    SimpleAgentTask,
    TaskConfig,
    TaskRunner,
    _serialize_result,
    enqueue_task,
)
from mts.providers.base import CompletionResult, LLMProvider
from mts.storage.sqlite_store import SQLiteStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class _MockProvider(LLMProvider):
    """Provider that returns configurable responses."""

    def __init__(self, responses: list[str] | None = None) -> None:
        self._responses = responses or ["Mock output"]
        self._call_idx = 0
        self.calls: list[dict] = []

    def complete(self, system_prompt, user_prompt, model=None, temperature=0.0, max_tokens=4096):
        self.calls.append({"system": system_prompt, "user": user_prompt, "model": model})
        text = self._responses[self._call_idx % len(self._responses)]
        self._call_idx += 1
        return CompletionResult(text=text, model=model or "mock")

    def default_model(self):
        return "mock-v1"


def _judge_response(score: float, reasoning: str = "ok") -> str:
    data = {"score": score, "reasoning": reasoning, "dimensions": {"quality": score}}
    return f"<!-- JUDGE_RESULT_START -->\n{json.dumps(data)}\n<!-- JUDGE_RESULT_END -->"


@pytest.fixture
def store(tmp_path):
    db = SQLiteStore(tmp_path / "test.db")
    migrations_dir = Path(__file__).parent.parent / "migrations"
    db.migrate(migrations_dir)
    return db


# ---------------------------------------------------------------------------
# TaskConfig
# ---------------------------------------------------------------------------

class TestTaskConfig:
    def test_from_none(self):
        cfg = TaskConfig.from_json(None)
        assert cfg.max_rounds == 5
        assert cfg.quality_threshold == 0.9

    def test_from_json(self):
        data = json.dumps({"max_rounds": 3, "quality_threshold": 0.8, "reference_context": "ref"})
        cfg = TaskConfig.from_json(data)
        assert cfg.max_rounds == 3
        assert cfg.quality_threshold == 0.8
        assert cfg.reference_context == "ref"

    def test_from_empty_string(self):
        cfg = TaskConfig.from_json("")
        assert cfg.max_rounds == 5


# ---------------------------------------------------------------------------
# Task Queue CRUD
# ---------------------------------------------------------------------------

class TestTaskQueue:
    def test_enqueue_and_get(self, store):
        store.enqueue_task("t1", "my_spec", priority=5)
        task = store.get_task("t1")
        assert task is not None
        assert task["spec_name"] == "my_spec"
        assert task["status"] == "pending"
        assert task["priority"] == 5

    def test_dequeue_returns_highest_priority(self, store):
        store.enqueue_task("low", "spec", priority=0)
        store.enqueue_task("high", "spec", priority=10)
        store.enqueue_task("mid", "spec", priority=5)

        task = store.dequeue_task()
        assert task is not None
        assert task["id"] == "high"
        assert task["status"] == "running"

    def test_dequeue_empty_returns_none(self, store):
        assert store.dequeue_task() is None

    def test_dequeue_skips_running(self, store):
        store.enqueue_task("t1", "spec")
        store.dequeue_task()  # t1 is now running
        assert store.dequeue_task() is None

    def test_complete_task(self, store):
        store.enqueue_task("t1", "spec")
        store.dequeue_task()
        store.complete_task("t1", best_score=0.95, best_output="great", total_rounds=3, met_threshold=True)
        task = store.get_task("t1")
        assert task["status"] == "completed"
        assert task["best_score"] == 0.95
        assert task["best_output"] == "great"
        assert task["total_rounds"] == 3
        assert task["met_threshold"] == 1

    def test_fail_task(self, store):
        store.enqueue_task("t1", "spec")
        store.dequeue_task()
        store.fail_task("t1", "something broke")
        task = store.get_task("t1")
        assert task["status"] == "failed"
        assert task["error"] == "something broke"

    def test_list_tasks(self, store):
        store.enqueue_task("t1", "spec_a")
        store.enqueue_task("t2", "spec_b")
        store.enqueue_task("t3", "spec_a")

        all_tasks = store.list_tasks()
        assert len(all_tasks) == 3

        a_tasks = store.list_tasks(spec_name="spec_a")
        assert len(a_tasks) == 2

    def test_list_tasks_by_status(self, store):
        store.enqueue_task("t1", "spec")
        store.enqueue_task("t2", "spec")
        store.dequeue_task()

        pending = store.list_tasks(status="pending")
        assert len(pending) == 1
        running = store.list_tasks(status="running")
        assert len(running) == 1

    def test_pending_count(self, store):
        assert store.pending_task_count() == 0
        store.enqueue_task("t1", "spec")
        store.enqueue_task("t2", "spec")
        assert store.pending_task_count() == 2
        store.dequeue_task()
        assert store.pending_task_count() == 1

    def test_enqueue_with_config(self, store):
        store.enqueue_task("t1", "spec", config={"max_rounds": 3, "rubric": "be good"})
        task = store.get_task("t1")
        config = json.loads(task["config_json"])
        assert config["max_rounds"] == 3
        assert config["rubric"] == "be good"


# ---------------------------------------------------------------------------
# SimpleAgentTask
# ---------------------------------------------------------------------------

class TestSimpleAgentTask:
    def test_generate_output(self):
        provider = _MockProvider(["Generated content"])
        task = SimpleAgentTask(task_prompt="Write something", rubric="Quality", provider=provider)
        output = task.generate_output({})
        assert output == "Generated content"

    def test_evaluate_output(self):
        provider = _MockProvider([_judge_response(0.85, "nice work")])
        task = SimpleAgentTask(task_prompt="Write something", rubric="Quality", provider=provider)
        result = task.evaluate_output("my output", {})
        assert result.score == 0.85

    def test_revise_output(self):
        provider = _MockProvider([_judge_response(0.5, "needs work"), "Revised content"])
        task = SimpleAgentTask(task_prompt="Write something", rubric="Quality", provider=provider)
        result = task.evaluate_output("bad output", {})
        revised = task.revise_output("bad output", result, {})
        assert revised == "Revised content"


# ---------------------------------------------------------------------------
# TaskRunner
# ---------------------------------------------------------------------------

class TestTaskRunner:
    def test_run_once_empty_queue(self, store):
        provider = _MockProvider()
        runner = TaskRunner(store=store, provider=provider)
        result = runner.run_once()
        assert result is None

    def test_run_once_processes_task(self, store):
        # Provider responses: generate, judge (score 0.95 to meet threshold)
        provider = _MockProvider([
            "Initial output",  # generate_output
            _judge_response(0.95, "excellent"),  # evaluate round 1
        ])
        config = {"task_prompt": "Write a haiku", "rubric": "Quality and form", "quality_threshold": 0.9}
        store.enqueue_task("t1", "haiku", config=config)

        runner = TaskRunner(store=store, provider=provider)
        result = runner.run_once()

        assert result is not None
        assert result["status"] == "completed"
        assert result["best_score"] == 0.95
        assert result["met_threshold"] == 1

    def test_run_once_with_revision(self, store):
        # Round 1: score 0.5 (below threshold), Round 2: revise then score 0.95
        provider = _MockProvider([
            "Initial output",                    # generate_output
            _judge_response(0.50, "needs work"),  # evaluate round 1
            "Revised output",                     # revise_output
            _judge_response(0.95, "excellent"),   # evaluate round 2
        ])
        config = {
            "task_prompt": "Write a poem",
            "rubric": "Quality",
            "quality_threshold": 0.9,
            "max_rounds": 3,
        }
        store.enqueue_task("t1", "poem", config=config)

        runner = TaskRunner(store=store, provider=provider)
        result = runner.run_once()

        assert result["status"] == "completed"
        assert result["best_score"] == 0.95
        assert result["total_rounds"] == 2

    def test_run_once_task_failure(self, store):
        # Provider that raises on first call
        class FailProvider(LLMProvider):
            def complete(self, *args, **kwargs):
                raise RuntimeError("API down")
            def default_model(self):
                return "fail"

        store.enqueue_task("t1", "spec", config={"task_prompt": "test", "rubric": "test"})
        runner = TaskRunner(store=store, provider=FailProvider())
        result = runner.run_once()

        assert result is not None
        assert result["status"] == "failed"
        assert "API down" in result["error"]

    def test_run_processes_multiple_then_stops(self, store):
        provider = _MockProvider([
            "Output 1", _judge_response(0.95),
            "Output 2", _judge_response(0.95),
        ])
        store.enqueue_task("t1", "spec", config={"task_prompt": "task 1", "rubric": "r"})
        store.enqueue_task("t2", "spec", config={"task_prompt": "task 2", "rubric": "r"})

        runner = TaskRunner(store=store, provider=provider, max_consecutive_empty=1, poll_interval=0.01)
        count = runner.run()
        assert count == 2

    def test_shutdown_stops_runner(self, store):
        provider = _MockProvider(["Output", _judge_response(0.95)])
        store.enqueue_task("t1", "spec", config={"task_prompt": "task", "rubric": "r"})

        runner = TaskRunner(store=store, provider=provider, poll_interval=0.01, max_consecutive_empty=1)
        runner.shutdown()  # Pre-shutdown
        count = runner.run()
        assert count == 0  # Should stop immediately

    def test_priority_ordering(self, store):
        provider = _MockProvider(["Output", _judge_response(0.95)] * 3)
        store.enqueue_task("low", "spec", priority=0, config={"task_prompt": "low", "rubric": "r"})
        store.enqueue_task("high", "spec", priority=10, config={"task_prompt": "high", "rubric": "r"})
        store.enqueue_task("mid", "spec", priority=5, config={"task_prompt": "mid", "rubric": "r"})

        runner = TaskRunner(store=store, provider=provider, max_consecutive_empty=1, poll_interval=0.01)
        # Process one at a time to verify order
        r1 = runner.run_once()
        r2 = runner.run_once()
        r3 = runner.run_once()
        assert r1["id"] == "high"
        assert r2["id"] == "mid"
        assert r3["id"] == "low"


# ---------------------------------------------------------------------------
# Enqueue convenience function
# ---------------------------------------------------------------------------

class TestEnqueueFunction:
    def test_enqueue_returns_id(self, store):
        task_id = enqueue_task(store, "my_spec", task_prompt="Do something", rubric="Quality")
        assert task_id is not None
        task = store.get_task(task_id)
        assert task["spec_name"] == "my_spec"
        assert task["status"] == "pending"

    def test_enqueue_with_all_options(self, store):
        task_id = enqueue_task(
            store, "spec",
            task_prompt="Write a post",
            rubric="Accuracy and voice",
            reference_context="RLM = Recursive Language Model",
            required_concepts=["context folding", "Python REPL"],
            max_rounds=3,
            quality_threshold=0.85,
            priority=5,
        )
        task = store.get_task(task_id)
        config = json.loads(task["config_json"])
        assert config["max_rounds"] == 3
        assert config["quality_threshold"] == 0.85
        assert "context folding" in config["required_concepts"]


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_serialize_result(self):
        result = ImprovementResult(
            rounds=[
                RoundResult(round_number=1, output="out1", score=0.5, reasoning="ok"),
                RoundResult(round_number=2, output="out2", score=0.9, reasoning="great", is_revision=True),
            ],
            best_output="out2",
            best_score=0.9,
            best_round=2,
            total_rounds=2,
            met_threshold=True,
        )
        serialized = _serialize_result(result)
        data = json.loads(serialized)
        assert data["best_score"] == 0.9
        assert data["met_threshold"] is True
        assert len(data["rounds"]) == 2
        assert data["rounds"][1]["is_revision"] is True
