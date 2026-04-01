"""MCP tool implementations — knowledge_tools (extracted from tools.py, AC-482)."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from autocontext.concepts import get_concept_model
from autocontext.execution.rubric_calibration import run_judge_calibration
from autocontext.mcp._base import _OPENCLAW_VERSION, MtsToolContext
from autocontext.scenarios import SCENARIO_REGISTRY
from autocontext.util.json_io import read_json

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    pass


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


def list_runs(ctx: MtsToolContext) -> list[dict[str, Any]]:  # type: ignore[override]
    """List recent runs from SQLite."""
    return ctx.sqlite.list_runs(limit=20)  # type: ignore[return-value]


def run_status(ctx: MtsToolContext, run_id: str) -> list[dict[str, Any]]:
    """Get generation-level metrics for a run."""
    return ctx.sqlite.get_generation_metrics(run_id)  # type: ignore[return-value]


def run_replay(ctx: MtsToolContext, run_id: str, generation: int) -> dict[str, Any]:
    """Read replay JSON for a specific generation."""

    replay_dir = ctx.settings.runs_root / run_id / "generations" / f"gen_{generation}" / "replays"
    if not replay_dir.exists():
        return {"error": f"no replay directory for run={run_id} gen={generation}"}
    replay_files = sorted(replay_dir.glob("*.json"))
    if not replay_files:
        return {"error": f"no replay files under {replay_dir}"}
    return read_json(replay_files[0])  # type: ignore[no-any-return]


def export_skill(ctx: MtsToolContext, scenario_name: str) -> dict[str, Any]:
    """Export a portable skill package for a solved scenario.

    Returns the structured package dict with two additional keys:
    - ``skill_markdown``: rendered SKILL.md ready for agent install
    - ``suggested_filename``: e.g. ``grid-ctf-knowledge.md``
    """
    from autocontext.knowledge.export import export_skill_package

    pkg = export_skill_package(ctx, scenario_name)
    result = pkg.to_dict()
    result["skill_markdown"] = pkg.to_skill_markdown()
    result["suggested_filename"] = f"{scenario_name.replace('_', '-')}-knowledge.md"
    return result


def list_solved(ctx: MtsToolContext) -> list[dict[str, Any]]:
    """List scenarios with solved strategies."""
    from autocontext.knowledge.export import list_solved_scenarios

    return list_solved_scenarios(ctx)


def search_strategies(ctx: MtsToolContext, query: str, top_k: int = 5) -> list[dict[str, Any]]:
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


def record_feedback(
    ctx: MtsToolContext,
    scenario_name: str,
    agent_output: str,
    human_score: float | None = None,
    human_notes: str = "",
    generation_id: str | None = None,
) -> dict[str, Any]:
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
) -> list[dict[str, Any]]:
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
) -> dict[str, Any]:
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
    from autocontext.providers.registry import get_provider

    calibration = ctx.sqlite.get_calibration_examples(scenario_name, limit=5)
    state = task.initial_state()

    loop = ImprovementLoop(
        task=task,
        max_rounds=max_rounds,
        quality_threshold=quality_threshold,
    )
    result = loop.run(
        initial_output=initial_output,
        state=state,
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

    rubric_calibration: dict[str, Any] | None = None
    if len(calibration) >= 2:
        provider = get_provider(ctx.settings)
        report = run_judge_calibration(
            domain=scenario_name,
            task_prompt=task.get_task_prompt(task.initial_state()),
            rubric=task.get_rubric(),
            provider=provider,
            model=ctx.settings.judge_model,
            calibration_examples=calibration,
            reference_context=reference_context,
            required_concepts=required_concepts,
        )
        rubric_calibration = report.to_dict() if report is not None else None

    payload: dict[str, Any] = {
        "scenario_name": scenario_name,
        "total_rounds": result.total_rounds,
        "met_threshold": result.met_threshold,
        "best_score": result.best_score,
        "best_round": result.best_round,
        "improved": result.improved,
        "rounds": rounds_summary,
        "best_output_preview": result.best_output[:500],
    }
    if result.pareto_frontier:
        payload["pareto_frontier"] = result.pareto_frontier
    if result.actionable_side_info:
        payload["actionable_side_info"] = result.actionable_side_info
    if result.metadata:
        payload["optimizer_metadata"] = result.metadata
    if ctx.settings.judge_samples > 1 or ctx.settings.judge_bias_probes_enabled:
        best_eval = task.evaluate_output(
            result.best_output,
            state,
            reference_context=reference_context,
            required_concepts=required_concepts,
            calibration_examples=calibration if calibration else None,
        )
        if best_eval.evaluator_guardrail is not None:
            payload["evaluator_guardrail"] = best_eval.evaluator_guardrail
            if not bool(best_eval.evaluator_guardrail.get("passed", True)):
                payload["met_threshold"] = False
    if rubric_calibration is not None:
        payload["rubric_calibration"] = rubric_calibration
    return payload


def export_package(ctx: MtsToolContext, scenario_name: str) -> dict[str, Any]:
    """Export a versioned, portable strategy package for a scenario."""
    from autocontext.knowledge.export import export_strategy_package

    try:
        pkg = export_strategy_package(ctx, scenario_name)
    except ValueError as exc:
        return {"error": str(exc)}
    return json.loads(pkg.to_json())  # type: ignore[no-any-return]


def import_package(
    ctx: MtsToolContext,
    package_data: dict[str, Any],
    conflict_policy: str = "merge",
) -> dict[str, Any]:
    """Import a strategy package into scenario knowledge."""
    from autocontext.knowledge.package import ConflictPolicy, StrategyPackage, import_strategy_package

    try:
        pkg = StrategyPackage.from_dict(package_data)
    except Exception as exc:
        logger.debug("mcp.tools: caught Exception", exc_info=True)
        return {"error": f"Invalid package data: {exc}"}
    try:
        policy = ConflictPolicy(conflict_policy)
    except ValueError:
        return {"error": f"Invalid conflict_policy: {conflict_policy!r}. Must be overwrite, merge, or skip."}
    result = import_strategy_package(ctx.artifacts, pkg, sqlite=ctx.sqlite, conflict_policy=policy)
    return result.model_dump()


def get_capabilities() -> dict[str, Any]:
    """Return capability metadata for this autocontext instance.

    Lists all available OpenClaw operations and their descriptions,
    enabling clients to discover what this autocontext instance can do.
    """
    return {
        "version": _OPENCLAW_VERSION,
        "concept_model": get_concept_model(),
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
