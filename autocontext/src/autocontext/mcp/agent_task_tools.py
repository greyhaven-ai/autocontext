"""MCP tool implementations — agent_task_tools (extracted from tools.py, AC-482)."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from autocontext.execution.evaluator_guardrail import evaluate_evaluator_guardrail
from autocontext.execution.objective_verification import run_objective_verification
from autocontext.execution.rubric_calibration import run_judge_calibration
from autocontext.execution.verification_dataset import (
    enrich_objective_payload,
    resolve_objective_verification_config,
)
from autocontext.harness.pipeline.objective_guardrail import (
    evaluate_objective_guardrail,
    resolve_objective_guardrail_policy,
)
from autocontext.mcp._base import MtsToolContext, _resolve_objective_verification, _validate_task_name
from autocontext.util.json_io import read_json, write_json

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    pass


def create_agent_task(
    ctx: MtsToolContext,
    name: str,
    task_prompt: str,
    rubric: str,
    reference_context: str | None = None,
    required_concepts: list[str] | None = None,
    generations: int = 1,
    max_rounds: int = 5,
    quality_threshold: float = 0.9,
    revision_prompt: str | None = None,
    objective_verification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create and register an agent task spec for evaluation."""

    if err := _validate_task_name(name):
        return {"error": err}

    spec_data = {
        "name": name,
        "task_prompt": task_prompt,
        "rubric": rubric,
        "reference_context": reference_context,
        "reference_sources": None,
        "required_concepts": required_concepts,
        "generations": generations,
        "max_rounds": max_rounds,
        "quality_threshold": quality_threshold,
        "revision_prompt": revision_prompt,
        "objective_verification": objective_verification,
    }

    # Persist to knowledge dir
    spec_dir = ctx.settings.knowledge_root / "_agent_tasks"
    spec_dir.mkdir(parents=True, exist_ok=True)
    spec_path = spec_dir / f"{name}.json"
    write_json(spec_path, spec_data)

    return {"name": name, "status": "created", "path": str(spec_path)}


def list_agent_tasks(ctx: MtsToolContext) -> list[dict[str, Any]]:
    """List all saved agent task specs."""

    spec_dir = ctx.settings.knowledge_root / "_agent_tasks"
    if not spec_dir.exists():
        return []

    tasks = []
    for spec_path in sorted(spec_dir.glob("*.json")):
        try:
            data = read_json(spec_path)
            tasks.append({
                "name": data.get("name", spec_path.stem),
                "task_prompt_preview": data.get("task_prompt", "")[:200],
                "generations": data.get("generations", 1),
                "quality_threshold": data.get("quality_threshold", 0.9),
                "max_rounds": data.get("max_rounds", 5),
                "has_reference_context": bool(data.get("reference_context")),
                "has_objective_verification": bool(data.get("objective_verification")),
            })
        except Exception:
            logger.debug("mcp.tools: caught Exception", exc_info=True)
            continue
    return tasks


def get_agent_task(ctx: MtsToolContext, name: str) -> dict[str, Any]:
    """Get full agent task spec by name."""

    if err := _validate_task_name(name):
        return {"error": err}

    spec_path = ctx.settings.knowledge_root / "_agent_tasks" / f"{name}.json"
    if not spec_path.exists():
        return {"error": f"Agent task '{name}' not found"}
    data: dict[str, Any] = read_json(spec_path)
    return data


def delete_agent_task(ctx: MtsToolContext, name: str) -> dict[str, Any]:
    """Delete an agent task spec."""
    if err := _validate_task_name(name):
        return {"error": err}

    spec_path = ctx.settings.knowledge_root / "_agent_tasks" / f"{name}.json"
    if not spec_path.exists():
        return {"error": f"Agent task '{name}' not found"}
    spec_path.unlink()
    return {"name": name, "status": "deleted"}


def evaluate_output(
    ctx: MtsToolContext,
    task_name: str,
    output: str,
) -> dict[str, Any]:
    """One-shot evaluation of an output against a saved agent task spec."""

    if err := _validate_task_name(task_name):
        return {"error": err}

    spec_path = ctx.settings.knowledge_root / "_agent_tasks" / f"{task_name}.json"
    if not spec_path.exists():
        return {"error": f"Agent task '{task_name}' not found"}

    data = read_json(spec_path)

    from autocontext.execution.judge import LLMJudge
    from autocontext.providers.registry import get_provider

    provider = get_provider(ctx.settings)
    judge = LLMJudge(
        model=ctx.settings.judge_model,
        rubric=data["rubric"],
        provider=provider,
        samples=ctx.settings.judge_samples,
        temperature=ctx.settings.judge_temperature,
        max_tokens=ctx.settings.judge_max_tokens,
        disagreement_threshold=ctx.settings.judge_disagreement_threshold,
    )

    calibration = ctx.sqlite.get_calibration_examples(task_name, limit=5)

    result = judge.evaluate(
        task_prompt=data["task_prompt"],
        agent_output=output,
        reference_context=data.get("reference_context"),
        required_concepts=data.get("required_concepts"),
        calibration_examples=calibration if calibration else None,
    )

    payload: dict[str, Any] = {
        "task_name": task_name,
        "score": result.score,
        "reasoning": result.reasoning,
        "dimension_scores": result.dimension_scores,
    }
    evaluator_guardrail = evaluate_evaluator_guardrail(
        result,
        provider=provider,
        model=ctx.settings.judge_model,
        rubric=data["rubric"],
        candidate_output=output,
        bias_probes_enabled=ctx.settings.judge_bias_probes_enabled,
    )
    if evaluator_guardrail is not None:
        payload["evaluator_guardrail"] = evaluator_guardrail.to_dict()
    objective_verification = data.get("objective_verification")
    if isinstance(objective_verification, dict):
        try:
            resolved = _resolve_objective_verification(ctx, objective_verification)
        except ValueError as exc:
            return {"error": str(exc)}
        if resolved:
            config, _dataset = resolve_objective_verification_config(resolved)
            if config is not None and config.ground_truth:
                verification_payload = run_objective_verification(
                    output=output,
                    rubric_score=result.score,
                    config=config,
                )
                payload["objective_verification"] = enrich_objective_payload(
                    verification_payload,
                )
                policy = resolve_objective_guardrail_policy(resolved)
                objective_payload = payload["objective_verification"]
                guardrail = evaluate_objective_guardrail(
                    objective_payload if isinstance(objective_payload, dict) else None,
                    policy,
                )
                if guardrail is not None:
                    payload["objective_guardrail"] = guardrail.to_dict()
    if len(calibration) >= 2:
        report = run_judge_calibration(
            domain=task_name,
            task_prompt=data["task_prompt"],
            rubric=data["rubric"],
            provider=provider,
            model=ctx.settings.judge_model,
            calibration_examples=calibration,
            reference_context=data.get("reference_context"),
            required_concepts=data.get("required_concepts"),
        )
        if report is not None:
            payload["rubric_calibration"] = report.to_dict()
    return payload


def generate_output(
    ctx: MtsToolContext,
    task_name: str,
) -> dict[str, Any]:
    """Generate an initial output for an agent task using the configured provider.

    This gives agents a starting point that can be fed into evaluate_output
    or run_improvement_loop.
    """

    if err := _validate_task_name(task_name):
        return {"error": err}

    spec_path = ctx.settings.knowledge_root / "_agent_tasks" / f"{task_name}.json"
    if not spec_path.exists():
        return {"error": f"Agent task '{task_name}' not found"}

    data = read_json(spec_path)

    from autocontext.providers.registry import get_provider

    provider = get_provider(ctx.settings)
    result = provider.complete(
        system_prompt="You are a skilled writer and analyst. Complete the task precisely and thoroughly.",
        user_prompt=data["task_prompt"],
    )

    return {
        "task_name": task_name,
        "output": result.text,
        "model": result.model,
    }


def queue_improvement_run(
    ctx: MtsToolContext,
    task_name: str,
    initial_output: str | None = None,
    priority: int = 0,
    browser_url: str | None = None,
) -> dict[str, Any]:
    """Add a task to the runner queue for background processing."""

    if err := _validate_task_name(task_name):
        return {"error": err}

    spec_path = ctx.settings.knowledge_root / "_agent_tasks" / f"{task_name}.json"
    if not spec_path.exists():
        return {"error": f"Agent task '{task_name}' not found"}

    data = read_json(spec_path)

    from autocontext.execution.task_runner import enqueue_task

    objective_verification = data.get("objective_verification")
    resolved_objective_verification: dict[str, Any] | None = None
    if isinstance(objective_verification, dict):
        try:
            resolved_objective_verification = _resolve_objective_verification(
                ctx,
                objective_verification,
            )
        except ValueError as exc:
            return {"error": str(exc)}

    task_id = enqueue_task(
        store=ctx.sqlite,
        spec_name=task_name,
        task_prompt=data.get("task_prompt"),
        rubric=data.get("rubric"),
        reference_context=data.get("reference_context"),
        browser_url=browser_url,
        required_concepts=data.get("required_concepts"),
        generations=data.get("generations", 1),
        max_rounds=data.get("max_rounds", 5),
        quality_threshold=data.get("quality_threshold", 0.9),
        initial_output=initial_output,
        objective_verification=resolved_objective_verification,
        judge_samples=ctx.settings.judge_samples,
        judge_temperature=ctx.settings.judge_temperature,
        judge_disagreement_threshold=ctx.settings.judge_disagreement_threshold,
        judge_bias_probes_enabled=ctx.settings.judge_bias_probes_enabled,
        priority=priority,
    )

    return {
        "task_id": task_id,
        "task_name": task_name,
        "status": "queued",
        "priority": priority,
        "generations": data.get("generations", 1),
    }


def get_queue_status(ctx: MtsToolContext) -> dict[str, Any]:
    """Get task queue status summary."""
    pending = ctx.sqlite.list_tasks(status="pending")
    running = ctx.sqlite.list_tasks(status="running")
    completed = ctx.sqlite.list_tasks(status="completed", limit=10)
    failed = ctx.sqlite.list_tasks(status="failed", limit=5)

    return {
        "pending_count": len(pending),
        "running_count": len(running),
        "recent_completed": [
            {"id": t["id"], "spec_name": t["spec_name"], "best_score": t.get("best_score"), "completed_at": t.get("completed_at")}
            for t in completed
        ],
        "recent_failed": [
            {"id": t["id"], "spec_name": t["spec_name"], "error_preview": (t.get("error") or "")[:200]}
            for t in failed
        ],
    }


def get_task_result(ctx: MtsToolContext, task_id: str) -> dict[str, Any]:
    """Get the result of a specific queued task."""

    task = ctx.sqlite.get_task(task_id)
    if not task:
        return {"error": f"Task '{task_id}' not found"}

    result: dict[str, Any] = {
        "id": task["id"],
        "spec_name": task["spec_name"],
        "status": task["status"],
        "priority": task["priority"],
        "created_at": task["created_at"],
    }

    if task["status"] == "completed":
        result["best_score"] = task["best_score"]
        result["total_rounds"] = task["total_rounds"]
        result["met_threshold"] = bool(task.get("met_threshold"))
        result["best_output"] = task["best_output"]
        result["completed_at"] = task["completed_at"]
        if task.get("result_json"):
            try:
                payload = json.loads(task["result_json"])
                result["rounds"] = payload.get("rounds", [])
                if "trajectory" in payload:
                    result["trajectory"] = payload["trajectory"]
                if "generations" in payload:
                    result["generations"] = payload["generations"]
                if "objective_verification" in payload:
                    result["objective_verification"] = payload["objective_verification"]
                if "objective_guardrail" in payload:
                    result["objective_guardrail"] = payload["objective_guardrail"]
                if "evaluator_guardrail" in payload:
                    result["evaluator_guardrail"] = payload["evaluator_guardrail"]
                if "rubric_calibration" in payload:
                    result["rubric_calibration"] = payload["rubric_calibration"]
            except (json.JSONDecodeError, AttributeError):
                result["rounds"] = []
    elif task["status"] == "failed":
        result["error"] = task.get("error", "")
    elif task["status"] == "running":
        result["started_at"] = task.get("started_at")

    return result


def get_best_output(ctx: MtsToolContext, task_name: str) -> dict[str, Any]:
    """Get the highest-scoring output for a task across all runs."""
    completed = ctx.sqlite.list_tasks(spec_name=task_name, status="completed")
    if not completed:
        return {"error": f"No completed runs for task '{task_name}'"}

    best = max(completed, key=lambda t: t.get("best_score") or 0.0)
    return {
        "task_name": task_name,
        "task_id": best["id"],
        "best_score": best.get("best_score"),
        "total_rounds": best.get("total_rounds"),
        "met_threshold": bool(best.get("met_threshold")),
        "best_output": best.get("best_output", ""),
        "completed_at": best.get("completed_at"),
    }


def export_agent_task_skill(
    ctx: MtsToolContext,
    task_name: str,
) -> dict[str, Any]:
    """Export a skill package for an agent task, including best outputs and lessons learned.

    Assembles results from completed task queue runs into a portable
    skill package that any agent can use.
    """

    if err := _validate_task_name(task_name):
        return {"error": err}

    spec_path = ctx.settings.knowledge_root / "_agent_tasks" / f"{task_name}.json"
    if not spec_path.exists():
        return {"error": f"Agent task '{task_name}' not found"}

    data = read_json(spec_path)

    # Gather completed runs for this task
    completed = ctx.sqlite.list_tasks(spec_name=task_name, status="completed")

    # Build example outputs from completed runs (top 3 by score)
    example_outputs = []
    for task_row in sorted(completed, key=lambda t: t.get("best_score") or 0.0, reverse=True)[:3]:
        example_outputs.append({
            "output": task_row.get("best_output", "")[:1000],
            "score": task_row.get("best_score", 0.0),
            "rounds": task_row.get("total_rounds", 0),
        })

    # Get human feedback/calibration if any
    feedback = ctx.sqlite.get_human_feedback(task_name, limit=10)
    lessons = []
    for fb in feedback:
        if fb.get("human_notes"):
            lessons.append(fb["human_notes"])

    best_score = max((t.get("best_score") or 0.0 for t in completed), default=0.0)
    best_output = ""
    if completed:
        best_row = max(completed, key=lambda t: t.get("best_score") or 0.0)
        best_output = best_row.get("best_output", "")

    skill_package = {
        "name": task_name,
        "task_prompt": data.get("task_prompt", ""),
        "rubric": data.get("rubric", ""),
        "reference_context": data.get("reference_context"),
        "required_concepts": data.get("required_concepts"),
        "best_score": best_score,
        "best_output": best_output,
        "total_runs": len(completed),
        "example_outputs": example_outputs,
        "lessons": lessons,
        "quality_threshold": data.get("quality_threshold", 0.9),
        "max_rounds": data.get("max_rounds", 5),
    }

    return skill_package
