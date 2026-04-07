"""MCP tool implementations — thin wrappers around existing autocontext infrastructure."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

# Re-exports for backward compatibility — consumers import from autocontext.mcp.tools
from autocontext.mcp._base import (  # noqa: F401
    _OPENCLAW_VERSION,
    MtsToolContext,
    _resolve_objective_verification,
    _validate_task_name,
)
from autocontext.mcp.agent_task_tools import (  # noqa: F401
    create_agent_task,
    delete_agent_task,
    evaluate_output,
    export_agent_task_skill,
    generate_output,
    get_agent_task,
    get_best_output,
    get_queue_status,
    get_task_result,
    list_agent_tasks,
    queue_improvement_run,
)
from autocontext.mcp.artifact_tools import (  # noqa: F401
    evaluate_strategy,
    fetch_artifact,
    list_artifacts,
    publish_artifact,
    validate_strategy_against_harness,
)
from autocontext.mcp.distill_tools import (  # noqa: F401
    distill_status,
    get_distill_job,
    trigger_distillation,
    update_distill_job,
)
from autocontext.mcp.knowledge_tools import (  # noqa: F401
    export_package,
    export_skill,
    get_capabilities,
    get_env_snapshot,
    get_evidence_list,
    get_feedback,
    import_package,
    list_runs,
    list_solved,
    read_analysis,
    read_hints,
    read_playbook,
    read_skills,
    read_tool_context,
    read_trajectory,
    record_feedback,
    run_improvement_loop,
    run_replay,
    run_status,
    search_strategies,
)
from autocontext.mcp.monitor_tools import (  # noqa: F401
    autocontext_create_monitor,
    autocontext_delete_monitor,
    autocontext_list_monitor_alerts,
    autocontext_list_monitors,
    autocontext_wait_for_monitor,
)
from autocontext.scenarios import SCENARIO_REGISTRY
from autocontext.scenarios.capabilities import (
    can_run_match,
    can_validate_actions,
    get_description,
    get_evaluation_criteria,
    get_rubric_safe,
    get_strategy_interface_safe,
)

logger = logging.getLogger(__name__)
if TYPE_CHECKING:
    pass

# -- Scenario exploration --


def list_scenarios() -> list[dict[str, str]]:
    """Return scenario names with descriptions."""
    results: list[dict[str, str]] = []
    for name, cls in SCENARIO_REGISTRY.items():
        instance = cls()
        preview = get_description(instance)[:200]
        results.append(
            {
                "name": name,
                "rules_preview": preview,
            }
        )
    return results


def describe_scenario(name: str) -> dict[str, str]:
    """Full scenario description: rules, strategy interface, evaluation criteria."""
    scenario = SCENARIO_REGISTRY[name]()
    return {
        "rules": get_description(scenario),
        "strategy_interface": get_strategy_interface_safe(scenario) or "",
        "evaluation_criteria": get_evaluation_criteria(scenario) or get_rubric_safe(scenario) or "",
    }


def validate_strategy(name: str, strategy: dict[str, Any]) -> dict[str, Any]:
    """Validate a strategy dict against scenario constraints."""
    scenario = SCENARIO_REGISTRY[name]()
    if not can_validate_actions(scenario):
        return {"valid": True, "reason": "Agent task scenarios use judge evaluation, not action validation"}
    state = scenario.initial_state(seed=42)
    valid, reason = scenario.validate_actions(state, "challenger", strategy)
    return {"valid": valid, "reason": reason}


def run_match(name: str, strategy: dict[str, Any], seed: int) -> dict[str, Any]:
    """Execute a single match, return Result as dict."""
    scenario = SCENARIO_REGISTRY[name]()
    if not can_run_match(scenario):
        return {"error": "Agent task scenarios use judge evaluation; use evaluate_output() instead"}
    result = scenario.execute_match(strategy, seed)
    return result.model_dump()  # type: ignore[no-any-return]


def run_tournament(name: str, strategy: dict[str, Any], matches: int, seed_base: int) -> dict[str, Any]:
    """Run N matches, return aggregate stats."""
    scenario = SCENARIO_REGISTRY[name]()
    if not can_run_match(scenario):
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

# -- Run management --

# -- Knowledge API --

# -- Human feedback --

# -- Agent Task Management --


# -- Task Queue --

# -- OpenClaw operations (AC-191) --

# -- Discovery & capability advertisement (AC-195) --


def skill_advertise_capabilities(ctx: MtsToolContext) -> dict[str, Any]:
    """Return full capability advertisement: version, runtime, scenarios, artifacts."""
    from autocontext.openclaw.discovery import advertise_capabilities

    ad = advertise_capabilities(ctx)
    return ad.model_dump()


def skill_scenario_capabilities(ctx: MtsToolContext, scenario_name: str) -> dict[str, Any]:
    """Return per-scenario capability info: evaluation mode, harness, playbook, etc."""
    from autocontext.openclaw.discovery import discover_scenario_capabilities

    caps = discover_scenario_capabilities(ctx, scenario_name)
    return caps.model_dump()


def skill_runtime_health(ctx: MtsToolContext) -> dict[str, Any]:
    """Return runtime health: executor mode, provider, harness mode, models."""
    from autocontext.openclaw.discovery import get_runtime_health

    health = get_runtime_health(ctx.settings)
    return health.model_dump()


def skill_scenario_artifact_lookup(ctx: MtsToolContext, scenario_name: str) -> list[dict[str, Any]]:
    """Return all artifacts associated with a scenario."""
    from autocontext.openclaw.discovery import scenario_artifact_lookup

    artifacts = scenario_artifact_lookup(ctx, scenario_name)
    return [a.model_dump() for a in artifacts]


# -- ClawHub skill wrapper functions (AC-192) --


def skill_manifest(ctx: MtsToolContext) -> dict[str, Any]:
    """Return the ClawHub skill manifest for this autocontext instance."""
    from autocontext.openclaw.skill import MtsSkillWrapper

    return MtsSkillWrapper(ctx).manifest().model_dump()


def skill_discover_scenarios(ctx: MtsToolContext, query: str | None = None) -> list[dict[str, Any]]:
    """Discover available scenarios, optionally filtered by query."""
    from autocontext.openclaw.skill import MtsSkillWrapper

    results = MtsSkillWrapper(ctx).discover_scenarios(query)
    return [r.model_dump() for r in results]


def skill_select_scenario(ctx: MtsToolContext, description: str) -> dict[str, Any]:
    """Recommend the best scenario for a problem description."""
    from autocontext.openclaw.skill import MtsSkillWrapper

    return MtsSkillWrapper(ctx).select_scenario(description).model_dump()


def skill_evaluate(
    ctx: MtsToolContext,
    scenario_name: str,
    strategy: dict[str, Any],
    num_matches: int = 3,
    seed_base: int = 42,
) -> dict[str, Any]:
    """Full validate + evaluate workflow."""
    from autocontext.openclaw.skill import MtsSkillWrapper

    return MtsSkillWrapper(ctx).evaluate(scenario_name, strategy, num_matches, seed_base).model_dump()


def skill_discover_artifacts(
    ctx: MtsToolContext,
    scenario: str | None = None,
    artifact_type: str | None = None,
) -> list[dict[str, Any]]:
    """Find published artifacts with enriched metadata."""
    from autocontext.openclaw.skill import MtsSkillWrapper

    results = MtsSkillWrapper(ctx).discover_artifacts(scenario, artifact_type)
    return [r.model_dump() for r in results]


# -- Monitor conditions (AC-209) --
