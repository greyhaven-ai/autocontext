"""Tests for MCP agent task management and queue tools."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autocontext.config.settings import AppSettings
from autocontext.mcp.tools import (
    MtsToolContext,
    create_agent_task,
    delete_agent_task,
    evaluate_output,
    get_agent_task,
    get_best_output,
    get_queue_status,
    get_task_result,
    list_agent_tasks,
    queue_improvement_run,
)
from autocontext.providers.base import CompletionResult, LLMProvider


class _MockProvider(LLMProvider):
    def __init__(self, response: str = "mock"):
        self._response = response
    def complete(self, system_prompt, user_prompt, model=None, temperature=0.0, max_tokens=4096):
        return CompletionResult(text=self._response, model=model or "mock")
    def default_model(self):
        return "mock"


def _judge_response(score: float = 0.85) -> str:
    data = {"score": score, "reasoning": "test", "dimensions": {"quality": score}}
    return f"<!-- JUDGE_RESULT_START -->\n{json.dumps(data)}\n<!-- JUDGE_RESULT_END -->"


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    """Create a test MtsToolContext with temp dirs."""
    settings = AppSettings(
        db_path=tmp_path / "test.db",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
    )
    context = MtsToolContext(settings)
    # Run migrations
    migrations_dir = Path(__file__).parent.parent / "migrations"
    context.sqlite.migrate(migrations_dir)
    return context


# ---------------------------------------------------------------------------
# Agent Task CRUD
# ---------------------------------------------------------------------------

class TestCreateAgentTask:
    def test_create_basic(self, ctx):
        result = create_agent_task(ctx, "test-task", "Write a poem", "Quality and creativity")
        assert result["status"] == "created"
        assert result["name"] == "test-task"

    def test_create_with_all_fields(self, ctx):
        result = create_agent_task(
            ctx, "full-task", "Write about RLMs",
            rubric="Accuracy",
            reference_context="RLM = Recursive Language Model",
            required_concepts=["context folding"],
            generations=3,
            max_rounds=3,
            quality_threshold=0.85,
            revision_prompt="Improve accuracy",
            objective_verification={
                "ground_truth": [
                    {
                        "item_id": "capital-france",
                        "description": "Paris is the capital of France",
                        "match_keywords": [["paris"], ["capital", "france"]],
                        "weight": "low",
                    }
                ]
            },
        )
        assert result["status"] == "created"
        # Verify persisted
        task = get_agent_task(ctx, "full-task")
        assert task["generations"] == 3
        assert task["reference_context"] == "RLM = Recursive Language Model"
        assert task["max_rounds"] == 3
        assert task["objective_verification"]["ground_truth"][0]["item_id"] == "capital-france"


class TestListAgentTasks:
    def test_empty(self, ctx):
        assert list_agent_tasks(ctx) == []

    def test_lists_created_tasks(self, ctx):
        create_agent_task(ctx, "task-a", "prompt a", "rubric a")
        create_agent_task(ctx, "task-b", "prompt b", "rubric b")
        tasks = list_agent_tasks(ctx)
        assert len(tasks) == 2
        names = {t["name"] for t in tasks}
        assert names == {"task-a", "task-b"}


class TestGetAgentTask:
    def test_not_found(self, ctx):
        result = get_agent_task(ctx, "nonexistent")
        assert "error" in result

    def test_get_existing(self, ctx):
        create_agent_task(ctx, "my-task", "Do something", "Be good")
        task = get_agent_task(ctx, "my-task")
        assert task["task_prompt"] == "Do something"
        assert task["rubric"] == "Be good"


class TestDeleteAgentTask:
    def test_delete_existing(self, ctx):
        create_agent_task(ctx, "to-delete", "prompt", "rubric")
        result = delete_agent_task(ctx, "to-delete")
        assert result["status"] == "deleted"
        assert "error" in get_agent_task(ctx, "to-delete")

    def test_delete_nonexistent(self, ctx):
        result = delete_agent_task(ctx, "nope")
        assert "error" in result


# ---------------------------------------------------------------------------
# Evaluate Output
# ---------------------------------------------------------------------------

class TestEvaluateOutput:
    def test_not_found(self, ctx):
        result = evaluate_output(ctx, "nonexistent", "output")
        assert "error" in result

    def test_evaluates_with_provider(self, ctx, monkeypatch):
        create_agent_task(ctx, "eval-task", "Write something", "Quality")

        # Mock the provider
        from autocontext.providers import registry
        monkeypatch.setattr(registry, "get_provider", lambda s: _MockProvider(_judge_response(0.88)))

        result = evaluate_output(ctx, "eval-task", "Here is my output")
        assert result["score"] == 0.88
        assert result["task_name"] == "eval-task"

    def test_evaluates_with_objective_verification(self, ctx, monkeypatch):
        create_agent_task(
            ctx,
            "l19-task",
            "Find serious drug interactions.",
            "Clinical accuracy",
            objective_verification={
                "ground_truth": [
                    {
                        "item_id": "warfarin-aspirin",
                        "description": "Warfarin + Aspirin",
                        "match_keywords": [["warfarin"], ["aspirin"]],
                        "weight": "high",
                    }
                ]
            },
        )

        from autocontext.providers import registry
        monkeypatch.setattr(registry, "get_provider", lambda s: _MockProvider(_judge_response(0.9)))

        result = evaluate_output(
            ctx,
            "l19-task",
            "1. Warfarin + Aspirin: high severity bleeding interaction.\n"
            "2. Vitamin C + Magnesium: benign supplement pairing.",
        )
        assert result["score"] == 0.9
        assert result["objective_verification"]["oracle_result"]["found_count"] == 1
        assert result["objective_verification"]["comparison"]["rubric_score"] == 0.9

    def test_evaluates_with_rubric_calibration(self, ctx, monkeypatch):
        create_agent_task(
            ctx,
            "calibrated-task",
            "Find serious drug interactions.",
            "Clinical accuracy",
        )
        ctx.sqlite.insert_human_feedback(
            scenario_name="calibrated-task",
            agent_output="Warfarin and aspirin increase bleeding risk.",
            human_score=0.82,
            human_notes="Strong anchor with correct interaction.",
        )
        ctx.sqlite.insert_human_feedback(
            scenario_name="calibrated-task",
            agent_output="Simvastatin and clarithromycin increase myopathy risk.",
            human_score=0.87,
            human_notes="Also correct and clinically relevant.",
        )

        from autocontext.providers import registry
        monkeypatch.setattr(registry, "get_provider", lambda s: _MockProvider(_judge_response(0.88)))

        result = evaluate_output(
            ctx,
            "calibrated-task",
            "1. Warfarin + Aspirin: high severity bleeding interaction.",
        )
        assert result["score"] == 0.88
        assert result["rubric_calibration"]["num_anchors"] == 2
        assert result["rubric_calibration"]["alignment"]["num_pairs"] == 2


# ---------------------------------------------------------------------------
# Queue tools
# ---------------------------------------------------------------------------

class TestQueueTools:
    def test_queue_not_found(self, ctx):
        result = queue_improvement_run(ctx, "nonexistent")
        assert "error" in result

    def test_queue_task(self, ctx):
        create_agent_task(ctx, "queue-task", "Do something", "Quality")
        result = queue_improvement_run(ctx, "queue-task", priority=5)
        assert result["status"] == "queued"
        assert result["priority"] == 5
        assert "task_id" in result
        assert result["generations"] == 1

    def test_queue_status_empty(self, ctx):
        status = get_queue_status(ctx)
        assert status["pending_count"] == 0
        assert status["running_count"] == 0

    def test_queue_status_with_tasks(self, ctx):
        create_agent_task(ctx, "s1", "prompt", "rubric")
        queue_improvement_run(ctx, "s1")
        queue_improvement_run(ctx, "s1")
        status = get_queue_status(ctx)
        assert status["pending_count"] == 2

    def test_get_task_result_not_found(self, ctx):
        result = get_task_result(ctx, "fake-id")
        assert "error" in result

    def test_get_task_result_pending(self, ctx):
        create_agent_task(ctx, "s1", "prompt", "rubric")
        queued = queue_improvement_run(ctx, "s1")
        result = get_task_result(ctx, queued["task_id"])
        assert result["status"] == "pending"

    def test_get_task_result_surfaces_multi_generation_trajectory(self, ctx):
        create_agent_task(ctx, "multi-task", "prompt", "rubric", generations=3)
        ctx.sqlite.enqueue_task(
            "t1",
            "multi-task",
            config={"task_prompt": "prompt", "rubric": "rubric", "generations": 3},
        )
        ctx.sqlite.dequeue_task()
        ctx.sqlite.complete_task(
            "t1",
            best_score=0.91,
            best_output="best output",
            total_rounds=5,
            met_threshold=True,
            result_json=json.dumps({
                "mode": "agent_task_multi_generation",
                "trajectory": {
                    "task_name": "multi-task",
                    "total_generations": 3,
                    "score_history": [0.4, 0.7, 0.91],
                    "lessons_per_generation": [1, 1, 1],
                    "cold_start_score": 0.4,
                    "final_score": 0.91,
                    "improvement_delta": 0.51,
                    "metadata": {},
                },
                "generations": [
                    {"generation": 1, "best_score": 0.4},
                    {"generation": 2, "best_score": 0.7},
                    {"generation": 3, "best_score": 0.91},
                ],
                "rounds": [{"round_number": 1, "score": 0.91, "reasoning": "ok"}],
            }),
        )

        result = get_task_result(ctx, "t1")
        assert result["status"] == "completed"
        assert result["trajectory"]["total_generations"] == 3
        assert len(result["generations"]) == 3

    def test_get_task_result_surfaces_objective_verification(self, ctx):
        create_agent_task(
            ctx,
            "l19-run",
            "Find drug interactions",
            "Clinical accuracy",
            objective_verification={
                "ground_truth": [
                    {
                        "item_id": "warfarin-aspirin",
                        "description": "Warfarin + Aspirin",
                        "match_keywords": [["warfarin"], ["aspirin"]],
                        "weight": "high",
                    }
                ]
            },
        )
        ctx.sqlite.enqueue_task(
            "t2",
            "l19-run",
            config={"task_prompt": "prompt", "rubric": "rubric"},
        )
        ctx.sqlite.dequeue_task()
        ctx.sqlite.complete_task(
            "t2",
            best_score=0.84,
            best_output="1. Warfarin + Aspirin: high severity bleeding interaction.",
            total_rounds=1,
            met_threshold=False,
            result_json=json.dumps({
                "rounds": [{"round_number": 1, "score": 0.84, "reasoning": "ok"}],
                "objective_verification": {
                    "oracle_result": {"found_count": 1, "precision": 1.0, "recall": 1.0},
                    "comparison": {"rubric_score": 0.84, "objective_recall": 1.0},
                },
            }),
        )

        result = get_task_result(ctx, "t2")
        assert result["objective_verification"]["oracle_result"]["found_count"] == 1
        assert result["objective_verification"]["comparison"]["rubric_score"] == 0.84

    def test_get_task_result_surfaces_rubric_calibration(self, ctx):
        create_agent_task(ctx, "calibrated-run", "prompt", "rubric")
        ctx.sqlite.enqueue_task(
            "t3",
            "calibrated-run",
            config={"task_prompt": "prompt", "rubric": "rubric"},
        )
        ctx.sqlite.dequeue_task()
        ctx.sqlite.complete_task(
            "t3",
            best_score=0.86,
            best_output="Warfarin + Aspirin is high risk.",
            total_rounds=1,
            met_threshold=False,
            result_json=json.dumps({
                "rounds": [{"round_number": 1, "score": 0.86, "reasoning": "ok"}],
                "rubric_calibration": {
                    "domain": "calibrated-run",
                    "num_anchors": 2,
                    "alignment": {"num_pairs": 2, "mean_absolute_error": 0.03},
                    "variance": {"std_dev": 0.0},
                    "calibrated": True,
                },
            }),
        )

        result = get_task_result(ctx, "t3")
        assert result["rubric_calibration"]["num_anchors"] == 2
        assert result["rubric_calibration"]["calibrated"] is True


class TestGetBestOutput:
    def test_no_completed(self, ctx):
        result = get_best_output(ctx, "nonexistent")
        assert "error" in result

    def test_returns_best(self, ctx):
        # Manually insert completed tasks
        ctx.sqlite.enqueue_task("t1", "my-task", config={"task_prompt": "p", "rubric": "r"})
        ctx.sqlite.dequeue_task()
        ctx.sqlite.complete_task("t1", best_score=0.70, best_output="ok output", total_rounds=2, met_threshold=False)

        ctx.sqlite.enqueue_task("t2", "my-task", config={"task_prompt": "p", "rubric": "r"})
        ctx.sqlite.dequeue_task()
        ctx.sqlite.complete_task("t2", best_score=0.95, best_output="great output", total_rounds=3, met_threshold=True)

        result = get_best_output(ctx, "my-task")
        assert result["best_score"] == 0.95
        assert result["best_output"] == "great output"
        assert result["met_threshold"] is True
