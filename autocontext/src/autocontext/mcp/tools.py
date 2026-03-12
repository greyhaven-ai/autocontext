"""MCP tool implementations — thin wrappers around existing AutoContext infrastructure."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, cast

from autocontext.config import AppSettings
from autocontext.execution.harness_loader import HarnessLoader
from autocontext.knowledge.trajectory import ScoreTrajectoryBuilder
from autocontext.scenarios import SCENARIO_REGISTRY
from autocontext.storage import ArtifactStore, SQLiteStore

if TYPE_CHECKING:
    from autocontext.openclaw.distill import DistillJob


class MtsToolContext:
    """Lazy-initialized shared state for MCP tool implementations."""

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.sqlite = SQLiteStore(settings.db_path)
        migrations_dir = Path(__file__).resolve().parents[3] / "migrations"
        if migrations_dir.exists():
            self.sqlite.migrate(migrations_dir)
        self.artifacts = ArtifactStore(
            settings.runs_root,
            settings.knowledge_root,
            settings.skills_root,
            settings.claude_skills_path,
            max_playbook_versions=settings.playbook_max_versions,
        )
        self.trajectory = ScoreTrajectoryBuilder(self.sqlite)


# -- Scenario exploration --


def list_scenarios() -> list[dict[str, str]]:
    """Return scenario names with descriptions."""
    results: list[dict[str, str]] = []
    for name, cls in SCENARIO_REGISTRY.items():
        instance = cls()
        if hasattr(instance, "describe_rules"):
            preview = instance.describe_rules()[:200]
        elif hasattr(instance, "describe_task"):
            preview = instance.describe_task()[:200]
        else:
            preview = ""
        results.append({
            "name": name,
            "rules_preview": preview,
        })
    return results


def describe_scenario(name: str) -> dict[str, str]:
    """Full scenario description: rules, strategy interface, evaluation criteria."""
    scenario = SCENARIO_REGISTRY[name]()
    if hasattr(scenario, "describe_rules"):
        return {
            "rules": scenario.describe_rules(),
            "strategy_interface": scenario.describe_strategy_interface(),
            "evaluation_criteria": scenario.describe_evaluation_criteria(),
        }
    return {
        "rules": scenario.describe_task() if hasattr(scenario, "describe_task") else "",
        "strategy_interface": "",
        "evaluation_criteria": scenario.get_rubric() if hasattr(scenario, "get_rubric") else "",
    }


def validate_strategy(name: str, strategy: dict[str, object]) -> dict[str, object]:
    """Validate a strategy dict against scenario constraints."""
    scenario = SCENARIO_REGISTRY[name]()
    if not hasattr(scenario, "validate_actions"):
        return {"valid": True, "reason": "Agent task scenarios use judge evaluation, not action validation"}
    state = scenario.initial_state(seed=42)
    valid, reason = scenario.validate_actions(state, "challenger", strategy)
    return {"valid": valid, "reason": reason}


def run_match(name: str, strategy: dict[str, object], seed: int) -> dict[str, object]:
    """Execute a single match, return Result as dict."""
    scenario = SCENARIO_REGISTRY[name]()
    if not hasattr(scenario, "execute_match"):
        return {"error": "Agent task scenarios use judge evaluation; use evaluate_output() instead"}
    result = scenario.execute_match(strategy, seed)
    return result.model_dump()


def run_tournament(name: str, strategy: dict[str, object], matches: int, seed_base: int) -> dict[str, object]:
    """Run N matches, return aggregate stats."""
    scenario = SCENARIO_REGISTRY[name]()
    if not hasattr(scenario, "execute_match"):
        return {"error": "Agent task scenarios use judge evaluation; use evaluate_output() instead"}
    scores: list[float] = []
    for i in range(matches):
        result = scenario.execute_match(strategy, seed_base + i)
        scores.append(result.score)
    return {
        "matches": matches,
        "scores": scores,
        "mean_score": sum(scores) / len(scores) if scores else 0.0,
        "best_score": max(scores) if scores else 0.0,
    }


# -- Knowledge reading --


def read_playbook(ctx: MtsToolContext, scenario_name: str) -> str:
    """Read current strategy playbook for a scenario."""
    return ctx.artifacts.read_playbook(scenario_name)


def read_trajectory(ctx: MtsToolContext, run_id: str) -> str:
    """Read score trajectory table for a run."""
    return ctx.trajectory.build_trajectory(run_id) or "No trajectory data yet."


def read_analysis(ctx: MtsToolContext, scenario_name: str, generation: int) -> str:
    """Read analysis for a specific generation."""
    analysis_path = ctx.artifacts.knowledge_root / scenario_name / "analysis" / f"gen_{generation}.md"
    if not analysis_path.exists():
        return ""
    return analysis_path.read_text(encoding="utf-8")


def read_hints(ctx: MtsToolContext, scenario_name: str) -> str:
    """Read persisted coach hints."""
    return ctx.artifacts.read_hints(scenario_name)


def read_tool_context(ctx: MtsToolContext, scenario_name: str) -> str:
    """Read architect-generated tools."""
    return ctx.artifacts.read_tool_context(scenario_name)


def read_skills(ctx: MtsToolContext, scenario_name: str) -> str:
    """Read operational lessons from SKILL.md."""
    return ctx.artifacts.read_skills(scenario_name)


# -- Run management --


def list_runs(ctx: MtsToolContext) -> list[dict[str, object]]:
    """List recent runs from SQLite."""
    with ctx.sqlite.connect() as conn:
        rows = conn.execute(
            "SELECT run_id, scenario, target_generations, executor_mode, status, created_at "
            "FROM runs ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
    return [dict(row) for row in rows]


def run_status(ctx: MtsToolContext, run_id: str) -> list[dict[str, object]]:
    """Get generation-level metrics for a run."""
    return ctx.sqlite.get_generation_metrics(run_id)


def run_replay(ctx: MtsToolContext, run_id: str, generation: int) -> dict[str, object]:
    """Read replay JSON for a specific generation."""
    import json

    replay_dir = ctx.settings.runs_root / run_id / "generations" / f"gen_{generation}" / "replays"
    if not replay_dir.exists():
        return {"error": f"no replay directory for run={run_id} gen={generation}"}
    replay_files = sorted(replay_dir.glob("*.json"))
    if not replay_files:
        return {"error": f"no replay files under {replay_dir}"}
    return json.loads(replay_files[0].read_text(encoding="utf-8"))  # type: ignore[no-any-return]


# -- Knowledge API --


def export_skill(ctx: MtsToolContext, scenario_name: str) -> dict[str, object]:
    """Export a portable skill package for a solved scenario."""
    from autocontext.knowledge.export import export_skill_package

    pkg = export_skill_package(ctx, scenario_name)
    return pkg.to_dict()


def list_solved(ctx: MtsToolContext) -> list[dict[str, object]]:
    """List scenarios with solved strategies."""
    from autocontext.knowledge.export import list_solved_scenarios

    return list_solved_scenarios(ctx)


def search_strategies(ctx: MtsToolContext, query: str, top_k: int = 5) -> list[dict[str, object]]:
    """Search solved scenarios by query."""
    from autocontext.knowledge.search import search_strategies as _search

    results = _search(ctx, query, top_k)
    return [
        {
            "scenario": r.scenario_name,
            "display_name": r.display_name,
            "description": r.description,
            "relevance": r.relevance_score,
            "best_score": r.best_score,
            "best_elo": r.best_elo,
            "match_reason": r.match_reason,
        }
        for r in results
    ]


# -- Human feedback --


def record_feedback(
    ctx: MtsToolContext,
    scenario_name: str,
    agent_output: str,
    human_score: float | None = None,
    human_notes: str = "",
    generation_id: str | None = None,
) -> dict[str, object]:
    """Record human feedback on an agent task output."""
    if not agent_output.strip():
        return {"error": "agent_output cannot be empty"}
    if human_score is not None and not (0.0 <= human_score <= 1.0):
        return {"error": f"human_score must be in [0.0, 1.0], got {human_score}"}
    row_id = ctx.sqlite.insert_human_feedback(
        scenario_name=scenario_name,
        agent_output=agent_output,
        human_score=human_score,
        human_notes=human_notes,
        generation_id=generation_id,
    )
    return {"id": row_id, "scenario_name": scenario_name, "status": "recorded"}


def get_feedback(
    ctx: MtsToolContext,
    scenario_name: str,
    limit: int = 10,
) -> list[dict[str, object]]:
    """Get recent human feedback for a scenario."""
    return ctx.sqlite.get_human_feedback(scenario_name, limit=limit)  # type: ignore[return-value]


def run_improvement_loop(
    ctx: MtsToolContext,
    scenario_name: str,
    initial_output: str,
    max_rounds: int = 5,
    quality_threshold: float = 0.9,
    reference_context: str | None = None,
    required_concepts: list[str] | None = None,
) -> dict[str, object]:
    """Run the multi-step improvement loop for an agent task.

    Evaluates and iteratively improves agent output until quality threshold
    is met or max rounds exhausted. Uses accumulated calibration examples.
    """
    if scenario_name not in SCENARIO_REGISTRY:
        supported = ", ".join(sorted(SCENARIO_REGISTRY.keys()))
        return {"error": f"Unknown scenario '{scenario_name}'. Available: {supported}"}

    from autocontext.scenarios.agent_task import AgentTaskInterface

    task = SCENARIO_REGISTRY[scenario_name]()
    if not isinstance(task, AgentTaskInterface):
        return {"error": f"'{scenario_name}' is not an agent task scenario. Improvement loops require agent task scenarios."}

    from autocontext.execution.improvement_loop import ImprovementLoop

    calibration = ctx.sqlite.get_calibration_examples(scenario_name, limit=5)

    loop = ImprovementLoop(
        task=task,
        max_rounds=max_rounds,
        quality_threshold=quality_threshold,
    )
    result = loop.run(
        initial_output=initial_output,
        state=task.initial_state(),
        reference_context=reference_context,
        required_concepts=required_concepts,
        calibration_examples=calibration if calibration else None,
    )

    rounds_summary = [
        {
            "round": r.round_number,
            "score": r.score,
            "is_revision": r.is_revision,
            "reasoning_preview": r.reasoning[:200],
        }
        for r in result.rounds
    ]

    return {
        "scenario_name": scenario_name,
        "total_rounds": result.total_rounds,
        "met_threshold": result.met_threshold,
        "best_score": result.best_score,
        "best_round": result.best_round,
        "improved": result.improved,
        "rounds": rounds_summary,
        "best_output_preview": result.best_output[:500],
    }


# -- Agent Task Management --

_TASK_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$")


def _validate_task_name(name: str) -> str | None:
    """Return an error message if the task name is invalid, else None."""
    if not name or not _TASK_NAME_RE.match(name):
        return "Invalid task name: must be 1-128 alphanumeric chars, hyphens, or underscores"
    return None


def create_agent_task(
    ctx: MtsToolContext,
    name: str,
    task_prompt: str,
    rubric: str,
    reference_context: str | None = None,
    required_concepts: list[str] | None = None,
    max_rounds: int = 5,
    quality_threshold: float = 0.9,
    revision_prompt: str | None = None,
) -> dict[str, object]:
    """Create and register an agent task spec for evaluation."""
    import json

    if err := _validate_task_name(name):
        return {"error": err}

    spec_data = {
        "name": name,
        "task_prompt": task_prompt,
        "rubric": rubric,
        "reference_context": reference_context,
        "reference_sources": None,
        "required_concepts": required_concepts,
        "max_rounds": max_rounds,
        "quality_threshold": quality_threshold,
        "revision_prompt": revision_prompt,
    }

    # Persist to knowledge dir
    spec_dir = ctx.settings.knowledge_root / "_agent_tasks"
    spec_dir.mkdir(parents=True, exist_ok=True)
    spec_path = spec_dir / f"{name}.json"
    spec_path.write_text(json.dumps(spec_data, indent=2), encoding="utf-8")

    return {"name": name, "status": "created", "path": str(spec_path)}


def list_agent_tasks(ctx: MtsToolContext) -> list[dict[str, object]]:
    """List all saved agent task specs."""
    import json

    spec_dir = ctx.settings.knowledge_root / "_agent_tasks"
    if not spec_dir.exists():
        return []

    tasks = []
    for spec_path in sorted(spec_dir.glob("*.json")):
        try:
            data = json.loads(spec_path.read_text(encoding="utf-8"))
            tasks.append({
                "name": data.get("name", spec_path.stem),
                "task_prompt_preview": data.get("task_prompt", "")[:200],
                "quality_threshold": data.get("quality_threshold", 0.9),
                "max_rounds": data.get("max_rounds", 5),
                "has_reference_context": bool(data.get("reference_context")),
            })
        except Exception:
            continue
    return tasks


def get_agent_task(ctx: MtsToolContext, name: str) -> dict[str, object]:
    """Get full agent task spec by name."""
    import json

    if err := _validate_task_name(name):
        return {"error": err}

    spec_path = ctx.settings.knowledge_root / "_agent_tasks" / f"{name}.json"
    if not spec_path.exists():
        return {"error": f"Agent task '{name}' not found"}
    data: dict[str, object] = json.loads(spec_path.read_text(encoding="utf-8"))
    return data


def delete_agent_task(ctx: MtsToolContext, name: str) -> dict[str, object]:
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
) -> dict[str, object]:
    """One-shot evaluation of an output against a saved agent task spec."""
    import json

    if err := _validate_task_name(task_name):
        return {"error": err}

    spec_path = ctx.settings.knowledge_root / "_agent_tasks" / f"{task_name}.json"
    if not spec_path.exists():
        return {"error": f"Agent task '{task_name}' not found"}

    data = json.loads(spec_path.read_text(encoding="utf-8"))

    from autocontext.execution.judge import LLMJudge
    from autocontext.providers.registry import get_provider

    provider = get_provider(ctx.settings)
    judge = LLMJudge(
        model=ctx.settings.judge_model,
        rubric=data["rubric"],
        provider=provider,
        samples=ctx.settings.judge_samples,
        temperature=ctx.settings.judge_temperature,
    )

    calibration = ctx.sqlite.get_calibration_examples(task_name, limit=5)

    result = judge.evaluate(
        task_prompt=data["task_prompt"],
        agent_output=output,
        reference_context=data.get("reference_context"),
        required_concepts=data.get("required_concepts"),
        calibration_examples=calibration if calibration else None,
    )

    return {
        "task_name": task_name,
        "score": result.score,
        "reasoning": result.reasoning,
        "dimension_scores": result.dimension_scores,
    }


def generate_output(
    ctx: MtsToolContext,
    task_name: str,
) -> dict[str, object]:
    """Generate an initial output for an agent task using the configured provider.

    This gives agents a starting point that can be fed into evaluate_output
    or run_improvement_loop.
    """
    import json

    if err := _validate_task_name(task_name):
        return {"error": err}

    spec_path = ctx.settings.knowledge_root / "_agent_tasks" / f"{task_name}.json"
    if not spec_path.exists():
        return {"error": f"Agent task '{task_name}' not found"}

    data = json.loads(spec_path.read_text(encoding="utf-8"))

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


# -- Task Queue --


def queue_improvement_run(
    ctx: MtsToolContext,
    task_name: str,
    initial_output: str | None = None,
    priority: int = 0,
) -> dict[str, object]:
    """Add a task to the runner queue for background processing."""
    import json

    if err := _validate_task_name(task_name):
        return {"error": err}

    spec_path = ctx.settings.knowledge_root / "_agent_tasks" / f"{task_name}.json"
    if not spec_path.exists():
        return {"error": f"Agent task '{task_name}' not found"}

    data = json.loads(spec_path.read_text(encoding="utf-8"))

    from autocontext.execution.task_runner import enqueue_task

    task_id = enqueue_task(
        store=ctx.sqlite,
        spec_name=task_name,
        task_prompt=data.get("task_prompt"),
        rubric=data.get("rubric"),
        reference_context=data.get("reference_context"),
        required_concepts=data.get("required_concepts"),
        max_rounds=data.get("max_rounds", 5),
        quality_threshold=data.get("quality_threshold", 0.9),
        initial_output=initial_output,
        priority=priority,
    )

    return {"task_id": task_id, "task_name": task_name, "status": "queued", "priority": priority}


def get_queue_status(ctx: MtsToolContext) -> dict[str, object]:
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


def get_task_result(ctx: MtsToolContext, task_id: str) -> dict[str, object]:
    """Get the result of a specific queued task."""
    import json

    task = ctx.sqlite.get_task(task_id)
    if not task:
        return {"error": f"Task '{task_id}' not found"}

    result: dict[str, object] = {
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
                result["rounds"] = json.loads(task["result_json"]).get("rounds", [])
            except (json.JSONDecodeError, AttributeError):
                result["rounds"] = []
    elif task["status"] == "failed":
        result["error"] = task.get("error", "")
    elif task["status"] == "running":
        result["started_at"] = task.get("started_at")

    return result


def get_best_output(ctx: MtsToolContext, task_name: str) -> dict[str, object]:
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
) -> dict[str, object]:
    """Export a skill package for an agent task, including best outputs and lessons learned.

    Assembles results from completed task queue runs into a portable
    skill package that any agent can use.
    """
    import json

    if err := _validate_task_name(task_name):
        return {"error": err}

    spec_path = ctx.settings.knowledge_root / "_agent_tasks" / f"{task_name}.json"
    if not spec_path.exists():
        return {"error": f"Agent task '{task_name}' not found"}

    data = json.loads(spec_path.read_text(encoding="utf-8"))

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


# -- OpenClaw operations (MTS-191) --


_OPENCLAW_VERSION = "0.1.0"


def evaluate_strategy(
    scenario_name: str,
    strategy: dict[str, object],
    num_matches: int = 3,
    seed_base: int = 42,
) -> dict[str, object]:
    """Evaluate a candidate strategy against a scenario by running matches.

    Returns aggregate scores for the strategy across multiple seeds.
    """
    if scenario_name not in SCENARIO_REGISTRY:
        supported = ", ".join(sorted(SCENARIO_REGISTRY.keys()))
        return {"error": f"Unknown scenario '{scenario_name}'. Available: {supported}"}

    scenario = SCENARIO_REGISTRY[scenario_name]()
    if not hasattr(scenario, "execute_match"):
        return {
            "error": (
                f"'{scenario_name}' is an agent task scenario. "
                "Use evaluate_output() for judge-based evaluation."
            )
        }

    scores: list[float] = []
    for i in range(num_matches):
        result = scenario.execute_match(strategy, seed_base + i)
        scores.append(result.score)

    return {
        "scenario": scenario_name,
        "matches": num_matches,
        "scores": scores,
        "mean_score": sum(scores) / len(scores) if scores else 0.0,
        "best_score": max(scores) if scores else 0.0,
    }


def validate_strategy_against_harness(
    scenario_name: str,
    strategy: dict[str, object],
    ctx: MtsToolContext | None = None,
) -> dict[str, object]:
    """Validate a strategy against scenario constraints and any harness validators.

    Checks both built-in scenario validation and any published harness artifacts.
    """
    if scenario_name not in SCENARIO_REGISTRY:
        supported = ", ".join(sorted(SCENARIO_REGISTRY.keys()))
        return {"error": f"Unknown scenario '{scenario_name}'. Available: {supported}"}

    scenario = SCENARIO_REGISTRY[scenario_name]()
    if not hasattr(scenario, "validate_actions"):
        return {
            "valid": True,
            "reason": "Agent task scenarios use judge evaluation, not action validation",
        }

    state = scenario.initial_state(seed=42)
    valid, reason = scenario.validate_actions(state, "challenger", strategy)
    harness_loaded: list[str] = []
    harness_errors: list[str] = []
    harness_passed = True

    if valid and ctx is not None:
        harness_loaded = _sync_published_harness_artifacts(ctx, scenario_name)
        harness_loader = HarnessLoader(
            ctx.artifacts.harness_dir(scenario_name),
            timeout_seconds=ctx.settings.harness_timeout_seconds,
        )
        harness_loaded = harness_loader.load()
        harness_result = harness_loader.validate_strategy(dict(strategy), scenario)
        harness_passed = harness_result.passed
        harness_errors = harness_result.errors

    return {
        "valid": valid and harness_passed,
        "reason": reason,
        "scenario": scenario_name,
        "harness_loaded": harness_loaded,
        "harness_passed": harness_passed,
        "harness_errors": harness_errors,
    }


def _sync_published_harness_artifacts(ctx: MtsToolContext, scenario_name: str) -> list[str]:
    """Mirror published harness artifacts into the runtime harness directory."""
    artifacts_dir = ctx.settings.knowledge_root / "_openclaw_artifacts"
    if not artifacts_dir.exists():
        return []

    synced: list[str] = []
    for artifact_path in sorted(artifacts_dir.glob("*.json")):
        try:
            artifact_data = json.loads(artifact_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if artifact_data.get("artifact_type") != "harness" or artifact_data.get("scenario") != scenario_name:
            continue
        source_code = artifact_data.get("source_code")
        artifact_id = artifact_data.get("id", artifact_path.stem)
        if not isinstance(source_code, str) or not source_code.strip():
            continue
        module_name = f"openclaw_{str(artifact_id).replace('-', '_')}"
        ctx.artifacts.write_harness(scenario_name, module_name, source_code)
        synced.append(module_name)
    return synced


def _validate_and_persist_artifact(
    ctx: MtsToolContext,
    artifact_data: dict[str, object],
    artifact_type: str,
) -> tuple[str, str]:
    """Validate artifact data and persist to disk. Returns (artifact_id, json_content)."""
    from autocontext.artifacts import DistilledModelArtifact, HarnessArtifact, PolicyArtifact

    validated: HarnessArtifact | PolicyArtifact | DistilledModelArtifact
    if artifact_type == "harness":
        validated = HarnessArtifact.model_validate(artifact_data)
    elif artifact_type == "policy":
        validated = PolicyArtifact.model_validate(artifact_data)
    else:
        validated = DistilledModelArtifact.model_validate(artifact_data)

    artifacts_dir = ctx.settings.knowledge_root / "_openclaw_artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifacts_dir / f"{validated.id}.json"
    artifact_path.write_text(validated.model_dump_json(indent=2), encoding="utf-8")
    if isinstance(validated, HarnessArtifact):
        ctx.artifacts.write_harness(validated.scenario, f"openclaw_{validated.id}", validated.source_code)

    return validated.id, str(artifact_path)


def publish_artifact(
    ctx: MtsToolContext,
    artifact_data: dict[str, object],
) -> dict[str, object]:
    """Publish an artifact (harness, policy, or distilled model) to the local store.

    The artifact_data must be a valid serialized artifact dict with an artifact_type field.
    """
    artifact_type = artifact_data.get("artifact_type")
    if artifact_type not in ("harness", "policy", "distilled_model"):
        return {
            "error": (
                f"Invalid or missing artifact_type: {artifact_type!r}. "
                "Must be harness, policy, or distilled_model."
            )
        }

    try:
        artifact_id, artifact_path = _validate_and_persist_artifact(ctx, artifact_data, str(artifact_type))
    except Exception as exc:
        return {"error": f"Invalid artifact data: {exc}"}

    return {
        "status": "published",
        "artifact_id": artifact_id,
        "artifact_type": str(artifact_type),
        "path": artifact_path,
    }


def fetch_artifact(
    ctx: MtsToolContext,
    artifact_id: str,
) -> dict[str, object]:
    """Fetch a published artifact by its ID."""
    import json as _json

    artifacts_dir = ctx.settings.knowledge_root / "_openclaw_artifacts"
    artifact_path = artifacts_dir / f"{artifact_id}.json"
    if not artifact_path.exists():
        return {"error": f"Artifact '{artifact_id}' not found"}

    data: dict[str, object] = _json.loads(artifact_path.read_text(encoding="utf-8"))
    return data


def list_artifacts(
    ctx: MtsToolContext,
    scenario: str | None = None,
    artifact_type: str | None = None,
) -> list[dict[str, object]]:
    """List published artifacts, optionally filtered by scenario or type."""
    import json as _json

    artifacts_dir = ctx.settings.knowledge_root / "_openclaw_artifacts"
    if not artifacts_dir.exists():
        return []

    results: list[dict[str, object]] = []
    for path in sorted(artifacts_dir.glob("*.json")):
        try:
            data: dict[str, object] = _json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if scenario and data.get("scenario") != scenario:
            continue
        if artifact_type and data.get("artifact_type") != artifact_type:
            continue
        results.append({
            "id": data.get("id", path.stem),
            "name": data.get("name", ""),
            "artifact_type": data.get("artifact_type", ""),
            "scenario": data.get("scenario", ""),
            "version": data.get("version", 0),
        })
    return results


def distill_status(
    ctx: MtsToolContext,
    scenario: str | None = None,
) -> dict[str, object]:
    """Return the status of distillation workflows.

    Uses DistillJobManager for structured job lifecycle tracking.
    Optionally filters by scenario name.
    """
    from autocontext.openclaw.distill import DistillJobManager

    mgr = DistillJobManager(ctx.settings.knowledge_root)
    jobs: list[DistillJob] = [_sync_distill_job(ctx, mgr, job) for job in mgr.list_jobs(scenario=scenario)]
    job_dicts: list[dict[str, object]] = [
        j.model_dump() for j in jobs
    ]
    active = sum(1 for j in jobs if j.status in ("pending", "running"))
    return {"active_jobs": active, "jobs": job_dicts}


def trigger_distillation(
    ctx: MtsToolContext,
    scenario: str,
    source_artifact_ids: list[str] | None = None,
    training_config: dict[str, object] | None = None,
) -> dict[str, object]:
    """Trigger a distillation workflow for a scenario.

    Creates a job record and launches the configured distillation sidecar.
    """
    from autocontext.openclaw.distill import DistillJobError, DistillJobManager, load_distill_sidecar

    mgr = DistillJobManager(ctx.settings.knowledge_root)
    job = mgr.create_job(
        scenario=scenario,
        source_artifact_ids=source_artifact_ids,
        training_config=dict(training_config) if training_config else None,
    )
    sidecar = load_distill_sidecar(ctx.settings, cwd=ctx.settings.knowledge_root.parent)
    if sidecar is None:
        failed = mgr.transition(
            job.job_id,
            "failed",
            error_message=(
                "No distillation sidecar configured. Set "
                "AUTOCONTEXT_OPENCLAW_DISTILL_SIDECAR_FACTORY or AUTOCONTEXT_OPENCLAW_DISTILL_SIDECAR_COMMAND."
            ),
        )
        assert failed is not None
        return {
            "error": failed.error_message,
            "job_id": failed.job_id,
            "status": failed.status,
            "scenario": failed.scenario,
        }
    try:
        sidecar.launch(job.job_id, job.scenario, job.training_config)
        launched_job = mgr.transition(job.job_id, "running")
    except (DistillJobError, OSError, ValueError) as exc:
        failed = mgr.transition(job.job_id, "failed", error_message=str(exc))
        assert failed is not None
        return {
            "error": str(exc),
            "job_id": failed.job_id,
            "status": failed.status,
            "scenario": failed.scenario,
        }
    if launched_job is None:
        return {"error": f"Distillation job '{job.job_id}' not found after launch"}
    return launched_job.model_dump()  # type: ignore[return-value]


def get_distill_job(
    ctx: MtsToolContext,
    job_id: str,
) -> dict[str, object]:
    """Fetch a single distillation job by ID."""
    from autocontext.openclaw.distill import DistillJobManager

    mgr = DistillJobManager(ctx.settings.knowledge_root)
    job = mgr.get_job(job_id)
    if job is None:
        return {"error": f"Distillation job '{job_id}' not found"}
    job = _sync_distill_job(ctx, mgr, job)
    return job.model_dump()  # type: ignore[return-value]


def update_distill_job(
    ctx: MtsToolContext,
    job_id: str,
    status: str,
    *,
    result_artifact_id: str | None = None,
    error_message: str | None = None,
    training_metrics: dict[str, object] | None = None,
) -> dict[str, object]:
    """Update a distillation job status with lifecycle validation."""
    from autocontext.openclaw.distill import DistillJobError, DistillJobManager

    mgr = DistillJobManager(ctx.settings.knowledge_root)
    try:
        job = mgr.transition(
            job_id,
            status,  # type: ignore[arg-type]
            result_artifact_id=result_artifact_id,
            error_message=error_message,
            training_metrics=dict(training_metrics) if training_metrics else None,
        )
    except DistillJobError as exc:
        return {"error": str(exc)}

    if job is None:
        return {"error": f"Distillation job '{job_id}' not found"}
    return job.model_dump()  # type: ignore[return-value]


def _sync_distill_job(
    ctx: MtsToolContext,
    mgr: object,
    job: object,
) -> DistillJob:
    """Poll the configured sidecar for an active job and persist any new state."""
    from autocontext.openclaw.distill import DistillJob, DistillJobError, DistillJobManager, load_distill_sidecar

    assert isinstance(mgr, DistillJobManager)
    assert isinstance(job, DistillJob)
    if job.status not in ("pending", "running"):
        return job
    sidecar = load_distill_sidecar(ctx.settings, cwd=ctx.settings.knowledge_root.parent)
    if sidecar is None:
        return job
    try:
        update = sidecar.poll(job.job_id)
    except Exception:
        return job
    status = update.get("status")
    if status not in ("pending", "running", "completed", "failed"):
        return job
    if status == job.status:
        return job
    try:
        synced = mgr.transition(
            job.job_id,
            status,
            result_artifact_id=cast(str | None, update.get("result_artifact_id")),
            error_message=cast(str | None, update.get("error_message")),
            training_metrics=cast(dict[str, object] | None, update.get("training_metrics")),
        )
    except DistillJobError:
        return job
    return synced or job


def export_package(ctx: MtsToolContext, scenario_name: str) -> dict[str, object]:
    """Export a versioned, portable strategy package for a scenario."""
    from autocontext.knowledge.export import export_strategy_package

    try:
        pkg = export_strategy_package(ctx, scenario_name)
    except ValueError as exc:
        return {"error": str(exc)}
    return cast(dict[str, object], json.loads(pkg.to_json()))


def import_package(
    ctx: MtsToolContext,
    package_data: dict[str, object],
    conflict_policy: str = "merge",
) -> dict[str, object]:
    """Import a strategy package into scenario knowledge."""
    from autocontext.knowledge.package import ConflictPolicy, StrategyPackage, import_strategy_package

    try:
        pkg = StrategyPackage.from_dict(package_data)
    except Exception as exc:
        return {"error": f"Invalid package data: {exc}"}
    try:
        policy = ConflictPolicy(conflict_policy)
    except ValueError:
        return {"error": f"Invalid conflict_policy: {conflict_policy!r}. Must be overwrite, merge, or skip."}
    result = import_strategy_package(ctx.artifacts, pkg, sqlite=ctx.sqlite, conflict_policy=policy)
    return result.model_dump()


def get_capabilities() -> dict[str, object]:
    """Return capability metadata for this AutoContext instance.

    Lists all available OpenClaw operations and their descriptions,
    enabling clients to discover what this AutoContext instance can do.
    """
    return {
        "version": _OPENCLAW_VERSION,
        "operations": [
            {
                "name": "evaluate_strategy",
                "description": "Evaluate a candidate strategy by running tournament matches",
                "input": "scenario_name, strategy, num_matches, seed_base",
            },
            {
                "name": "validate_strategy",
                "description": "Validate a strategy against scenario constraints and harness validators",
                "input": "scenario_name, strategy",
            },
            {
                "name": "publish_artifact",
                "description": "Publish a harness, policy, or distilled model artifact",
                "input": "artifact_data (serialized artifact dict)",
            },
            {
                "name": "fetch_artifact",
                "description": "Fetch a published artifact by ID",
                "input": "artifact_id",
            },
            {
                "name": "list_artifacts",
                "description": "List published artifacts with optional filters",
                "input": "scenario (optional), artifact_type (optional)",
            },
            {
                "name": "distill_status",
                "description": "Check status of distillation workflows",
                "input": "(none)",
            },
            {
                "name": "trigger_distillation",
                "description": "Trigger a distillation workflow for a scenario",
                "input": "scenario, source_artifact_ids (optional)",
            },
        ],
    }

# -- Discovery & capability advertisement (AC-195) --


def skill_advertise_capabilities(ctx: MtsToolContext) -> dict[str, object]:
    """Return full capability advertisement: version, runtime, scenarios, artifacts."""
    from autocontext.openclaw.discovery import advertise_capabilities

    ad = advertise_capabilities(ctx)
    return cast(dict[str, object], ad.model_dump())


def skill_scenario_capabilities(ctx: MtsToolContext, scenario_name: str) -> dict[str, object]:
    """Return per-scenario capability info: evaluation mode, harness, playbook, etc."""
    from autocontext.openclaw.discovery import discover_scenario_capabilities

    caps = discover_scenario_capabilities(ctx, scenario_name)
    return cast(dict[str, object], caps.model_dump())


def skill_runtime_health(ctx: MtsToolContext) -> dict[str, object]:
    """Return runtime health: executor mode, provider, harness mode, models."""
    from autocontext.openclaw.discovery import get_runtime_health

    health = get_runtime_health(ctx.settings)
    return cast(dict[str, object], health.model_dump())


def skill_scenario_artifact_lookup(ctx: MtsToolContext, scenario_name: str) -> list[dict[str, object]]:
    """Return all artifacts associated with a scenario."""
    from autocontext.openclaw.discovery import scenario_artifact_lookup

    artifacts = scenario_artifact_lookup(ctx, scenario_name)
    return [cast(dict[str, object], a.model_dump()) for a in artifacts]


# -- ClawHub skill wrapper functions (AC-192) --


def skill_manifest(ctx: MtsToolContext) -> dict[str, object]:
    """Return the ClawHub skill manifest for this AutoContext instance."""
    from autocontext.openclaw.skill import MtsSkillWrapper

    return MtsSkillWrapper(ctx).manifest().model_dump()


def skill_discover_scenarios(ctx: MtsToolContext, query: str | None = None) -> list[dict[str, object]]:
    """Discover available scenarios, optionally filtered by query."""
    from autocontext.openclaw.skill import MtsSkillWrapper

    results = MtsSkillWrapper(ctx).discover_scenarios(query)
    return [r.model_dump() for r in results]


def skill_select_scenario(ctx: MtsToolContext, description: str) -> dict[str, object]:
    """Recommend the best scenario for a problem description."""
    from autocontext.openclaw.skill import MtsSkillWrapper

    return MtsSkillWrapper(ctx).select_scenario(description).model_dump()


def skill_evaluate(
    ctx: MtsToolContext,
    scenario_name: str,
    strategy: dict[str, object],
    num_matches: int = 3,
    seed_base: int = 42,
) -> dict[str, object]:
    """Full validate + evaluate workflow."""
    from autocontext.openclaw.skill import MtsSkillWrapper

    return MtsSkillWrapper(ctx).evaluate(scenario_name, strategy, num_matches, seed_base).model_dump()


def skill_discover_artifacts(
    ctx: MtsToolContext,
    scenario: str | None = None,
    artifact_type: str | None = None,
) -> list[dict[str, object]]:
    """Find published artifacts with enriched metadata."""
    from autocontext.openclaw.skill import MtsSkillWrapper

    results = MtsSkillWrapper(ctx).discover_artifacts(scenario, artifact_type)
    return [r.model_dump() for r in results]


# -- Monitor conditions (AC-209) --


def autocontext_create_monitor(
    name: str,
    condition_type: str,
    params_json: str = "{}",
    scope: str = "global",
) -> dict[str, object]:
    """Create a new monitor condition."""
    from autocontext.monitor.engine import get_engine
    from autocontext.monitor.types import ConditionType, MonitorCondition, make_id

    engine = get_engine()
    cid = make_id()
    params = json.loads(params_json) if isinstance(params_json, str) else params_json
    cond = MonitorCondition(
        id=cid,
        name=name,
        condition_type=ConditionType(condition_type),
        params=params,
        scope=scope,
    )
    engine.create_condition(cond)
    return {"id": cid, "name": name, "condition_type": condition_type, "scope": scope}


def autocontext_list_monitors(
    scope: str | None = None,
    active_only: bool = True,
) -> list[dict[str, object]]:
    """List monitor conditions."""
    from autocontext.monitor.engine import get_engine

    engine = get_engine()
    return engine._sqlite.list_monitor_conditions(active_only=active_only, scope=scope)


def autocontext_delete_monitor(condition_id: str) -> dict[str, object]:
    """Deactivate a monitor condition."""
    from autocontext.monitor.engine import get_engine

    engine = get_engine()
    found = engine._sqlite.deactivate_monitor_condition(condition_id)
    return {"deleted": found, "condition_id": condition_id}


def autocontext_list_monitor_alerts(
    condition_id: str | None = None,
    scope: str | None = None,
    limit: int = 100,
) -> list[dict[str, object]]:
    """List monitor alerts."""
    from autocontext.monitor.engine import get_engine

    engine = get_engine()
    return engine._sqlite.list_monitor_alerts(condition_id=condition_id, scope=scope, limit=limit)


def autocontext_wait_for_monitor(
    condition_id: str,
    timeout_seconds: float = 30.0,
) -> dict[str, object]:
    """Wait for a monitor condition to fire."""
    from autocontext.monitor.engine import get_engine

    engine = get_engine()
    fired = engine.wait_for_alert(condition_id, timeout=timeout_seconds)
    alert = None
    if fired:
        alerts = engine._sqlite.list_monitor_alerts(condition_id=condition_id, limit=1)
        if alerts:
            alert = alerts[0]
    return {"fired": fired, "alert": alert}
