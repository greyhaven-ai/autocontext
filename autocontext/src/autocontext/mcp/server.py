"""AutoContext MCP server — exposes scenario, knowledge, and run tools via stdio."""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from autocontext.config import load_settings
from autocontext.mcp import tools
from autocontext.mcp.sandbox import SandboxManager

mcp = FastMCP("autocontext")
_ctx: tools.MtsToolContext | None = None
_sandbox_mgr: SandboxManager | None = None
_solve_mgr: object | None = None


def _get_ctx() -> tools.MtsToolContext:
    global _ctx
    if _ctx is None:
        _ctx = tools.MtsToolContext(load_settings())
    return _ctx


def _get_sandbox_mgr() -> SandboxManager:
    global _sandbox_mgr
    if _sandbox_mgr is None:
        _sandbox_mgr = SandboxManager(_get_ctx().settings)
    return _sandbox_mgr


def _get_solve_mgr() -> object:
    from autocontext.knowledge.solver import SolveManager

    global _solve_mgr
    if _solve_mgr is None:
        _solve_mgr = SolveManager(_get_ctx().settings)
    return _solve_mgr


# -- Scenario exploration tools --


@mcp.tool()
def autocontext_list_scenarios() -> str:
    """List available game scenarios with rules preview."""
    return json.dumps(tools.list_scenarios())


@mcp.tool()
def autocontext_describe_scenario(scenario_name: str) -> str:
    """Get full scenario description: rules, strategy interface, evaluation criteria."""
    return json.dumps(tools.describe_scenario(scenario_name))


@mcp.tool()
def autocontext_validate_strategy(scenario_name: str, strategy: str) -> str:
    """Validate a strategy JSON string against scenario constraints."""
    return json.dumps(tools.validate_strategy(scenario_name, json.loads(strategy)))


@mcp.tool()
def autocontext_run_match(scenario_name: str, strategy: str, seed: int = 42) -> str:
    """Execute a single match and return the result."""
    return json.dumps(tools.run_match(scenario_name, json.loads(strategy), seed))


@mcp.tool()
def autocontext_run_tournament(scenario_name: str, strategy: str, matches: int = 3, seed_base: int = 1000) -> str:
    """Run N matches and return aggregate stats."""
    return json.dumps(tools.run_tournament(scenario_name, json.loads(strategy), matches, seed_base))


# -- Knowledge reading tools --


@mcp.tool()
def autocontext_read_playbook(scenario_name: str) -> str:
    """Read current strategy playbook for a scenario."""
    return tools.read_playbook(_get_ctx(), scenario_name)


@mcp.tool()
def autocontext_read_trajectory(run_id: str) -> str:
    """Read score trajectory table for a run."""
    return tools.read_trajectory(_get_ctx(), run_id)


@mcp.tool()
def autocontext_read_analysis(scenario_name: str, generation: int) -> str:
    """Read analysis for a specific generation."""
    return tools.read_analysis(_get_ctx(), scenario_name, generation)


@mcp.tool()
def autocontext_read_hints(scenario_name: str) -> str:
    """Read persisted coach hints for a scenario."""
    return tools.read_hints(_get_ctx(), scenario_name)


@mcp.tool()
def autocontext_read_tools(scenario_name: str) -> str:
    """Read architect-generated tools for a scenario."""
    return tools.read_tool_context(_get_ctx(), scenario_name)


@mcp.tool()
def autocontext_read_skills(scenario_name: str) -> str:
    """Read operational lessons from SKILL.md for a scenario."""
    return tools.read_skills(_get_ctx(), scenario_name)


# -- Run management tools --


@mcp.tool()
def autocontext_list_runs() -> str:
    """List recent runs."""
    return json.dumps(tools.list_runs(_get_ctx()))


@mcp.tool()
def autocontext_run_status(run_id: str) -> str:
    """Get generation-level metrics for a run."""
    return json.dumps(tools.run_status(_get_ctx(), run_id))


@mcp.tool()
def autocontext_run_replay(run_id: str, generation: int) -> str:
    """Read replay JSON for a specific generation."""
    return json.dumps(tools.run_replay(_get_ctx(), run_id, generation))


# -- Sandbox tools --


@mcp.tool()
def autocontext_sandbox_create(scenario_name: str, user_id: str = "anonymous") -> str:
    """Create an isolated sandbox for external play."""
    mgr = _get_sandbox_mgr()
    sandbox = mgr.create(scenario_name, user_id)
    return json.dumps({"sandbox_id": sandbox.sandbox_id, "scenario_name": sandbox.scenario_name, "user_id": sandbox.user_id})


@mcp.tool()
def autocontext_sandbox_run(sandbox_id: str, generations: int = 1) -> str:
    """Run generation(s) in a sandbox."""
    mgr = _get_sandbox_mgr()
    result = mgr.run_generation(sandbox_id, generations)
    return json.dumps(result)


@mcp.tool()
def autocontext_sandbox_status(sandbox_id: str) -> str:
    """Get sandbox status."""
    mgr = _get_sandbox_mgr()
    return json.dumps(mgr.get_status(sandbox_id))


@mcp.tool()
def autocontext_sandbox_playbook(sandbox_id: str) -> str:
    """Read sandbox playbook."""
    mgr = _get_sandbox_mgr()
    return mgr.read_playbook(sandbox_id)


@mcp.tool()
def autocontext_sandbox_list() -> str:
    """List active sandboxes."""
    mgr = _get_sandbox_mgr()
    return json.dumps(mgr.list_sandboxes())


@mcp.tool()
def autocontext_sandbox_destroy(sandbox_id: str) -> str:
    """Destroy a sandbox and clean up its data."""
    mgr = _get_sandbox_mgr()
    destroyed = mgr.destroy(sandbox_id)
    return json.dumps({"destroyed": destroyed, "sandbox_id": sandbox_id})


# -- Knowledge API tools --


@mcp.tool()
def autocontext_export_skill(scenario_name: str) -> str:
    """Export a portable skill package (playbook + lessons + strategy) for a solved scenario.
    Returns markdown + JSON that can be dropped into any agent's skill directory."""
    return json.dumps(tools.export_skill(_get_ctx(), scenario_name))


@mcp.tool()
def autocontext_list_solved() -> str:
    """List all scenarios with solved strategies, including best scores and run counts."""
    return json.dumps(tools.list_solved(_get_ctx()))


@mcp.tool()
def autocontext_search_strategies(query: str, top_k: int = 5) -> str:
    """Search solved scenarios by natural language query.
    Returns ranked results with relevance scores."""
    return json.dumps(tools.search_strategies(_get_ctx(), query, top_k))


@mcp.tool()
def autocontext_solve_scenario(description: str, generations: int = 5) -> str:
    """Submit a new problem for on-demand solving. MTS creates a scenario from the
    description, runs strategy evolution, and produces a skill package.
    Returns a job_id for polling status."""
    from autocontext.knowledge.solver import SolveManager

    mgr: SolveManager = _get_solve_mgr()  # type: ignore[assignment]
    job_id = mgr.submit(description, generations)
    return json.dumps({"job_id": job_id, "status": "pending"})


@mcp.tool()
def autocontext_solve_status(job_id: str) -> str:
    """Check status of a solve-on-demand job."""
    from autocontext.knowledge.solver import SolveManager

    mgr: SolveManager = _get_solve_mgr()  # type: ignore[assignment]
    return json.dumps(mgr.get_status(job_id))


@mcp.tool()
def autocontext_solve_result(job_id: str) -> str:
    """Get the skill package result of a completed solve job."""
    from autocontext.knowledge.solver import SolveManager

    mgr: SolveManager = _get_solve_mgr()  # type: ignore[assignment]
    pkg = mgr.get_result(job_id)
    if pkg is None:
        return json.dumps({"error": "Job not completed or not found"})
    return json.dumps(pkg.to_dict())


# -- Human feedback tools --


@mcp.tool()
def autocontext_record_feedback(
    scenario_name: str,
    agent_output: str,
    human_score: float | None = None,
    human_notes: str = "",
    generation_id: str | None = None,
) -> str:
    """Record human feedback on an agent task output. Score should be 0.0-1.0."""
    return json.dumps(tools.record_feedback(
        _get_ctx(), scenario_name, agent_output, human_score, human_notes, generation_id
    ))


@mcp.tool()
def autocontext_get_feedback(scenario_name: str, limit: int = 10) -> str:
    """Get recent human feedback for a scenario."""
    return json.dumps(tools.get_feedback(_get_ctx(), scenario_name, limit))


@mcp.tool()
def autocontext_run_improvement_loop(
    scenario_name: str,
    initial_output: str,
    max_rounds: int = 5,
    quality_threshold: float = 0.9,
    reference_context: str | None = None,
    required_concepts: str | None = None,
) -> str:
    """Run the multi-step improvement loop for an agent task.
    Iteratively evaluates and revises output until quality threshold or max rounds."""
    try:
        concepts = json.loads(required_concepts) if required_concepts else None
    except json.JSONDecodeError:
        return json.dumps({"error": "Invalid JSON in required_concepts parameter"})
    return json.dumps(tools.run_improvement_loop(
        _get_ctx(), scenario_name, initial_output, max_rounds,
        quality_threshold, reference_context, concepts,
    ))


# -- Agent Task Management tools --


@mcp.tool()
def autocontext_create_agent_task(
    name: str,
    task_prompt: str,
    rubric: str,
    reference_context: str | None = None,
    required_concepts: str | None = None,
    max_rounds: int = 5,
    quality_threshold: float = 0.9,
    revision_prompt: str | None = None,
) -> str:
    """Create and save an agent task spec for evaluation.
    required_concepts is a JSON array of strings."""
    try:
        concepts = json.loads(required_concepts) if required_concepts else None
    except json.JSONDecodeError:
        return json.dumps({"error": "Invalid JSON in required_concepts parameter"})
    return json.dumps(tools.create_agent_task(
        _get_ctx(), name, task_prompt, rubric, reference_context,
        concepts, max_rounds, quality_threshold, revision_prompt,
    ))


@mcp.tool()
def autocontext_list_agent_tasks() -> str:
    """List all saved agent task specs."""
    return json.dumps(tools.list_agent_tasks(_get_ctx()))


@mcp.tool()
def autocontext_get_agent_task(name: str) -> str:
    """Get full agent task spec by name."""
    return json.dumps(tools.get_agent_task(_get_ctx(), name))


@mcp.tool()
def autocontext_delete_agent_task(name: str) -> str:
    """Delete an agent task spec."""
    return json.dumps(tools.delete_agent_task(_get_ctx(), name))


@mcp.tool()
def autocontext_evaluate_output(task_name: str, output: str) -> str:
    """One-shot evaluation of an output against a saved agent task spec.
    Returns score, reasoning, and dimension scores."""
    return json.dumps(tools.evaluate_output(_get_ctx(), task_name, output))


@mcp.tool()
def autocontext_generate_output(task_name: str) -> str:
    """Generate an initial output for an agent task. Returns the generated text."""
    ctx = _get_ctx()
    result = tools.generate_output(ctx, task_name)
    return json.dumps(result, indent=2, default=str)


# -- Task Queue tools --


@mcp.tool()
def autocontext_queue_improvement_run(
    task_name: str,
    initial_output: str | None = None,
    priority: int = 0,
) -> str:
    """Queue an agent task for background improvement loop processing.
    The task runner daemon will pick it up automatically."""
    return json.dumps(tools.queue_improvement_run(_get_ctx(), task_name, initial_output, priority))


@mcp.tool()
def autocontext_get_queue_status() -> str:
    """Get task queue status: pending, running, recent completed/failed."""
    return json.dumps(tools.get_queue_status(_get_ctx()))


@mcp.tool()
def autocontext_get_task_result(task_id: str) -> str:
    """Get the result of a specific queued task by ID."""
    return json.dumps(tools.get_task_result(_get_ctx(), task_id))


@mcp.tool()
def autocontext_get_best_output(task_name: str) -> str:
    """Get the highest-scoring output for a task across all completed runs."""
    return json.dumps(tools.get_best_output(_get_ctx(), task_name))


@mcp.tool()
def autocontext_export_agent_task_skill(task_name: str) -> str:
    """Export a portable skill package for an agent task with best outputs and lessons."""
    ctx = _get_ctx()
    result = tools.export_agent_task_skill(ctx, task_name)
    return json.dumps(result, indent=2, default=str)


# -- OpenClaw tools (MTS-191) --


@mcp.tool()
def autocontext_evaluate_strategy(
    scenario_name: str,
    strategy: str,
    num_matches: int = 3,
    seed_base: int = 42,
) -> str:
    """Evaluate a candidate strategy against a scenario by running tournament matches.
    strategy should be a JSON string."""
    return json.dumps(tools.evaluate_strategy(
        scenario_name, json.loads(strategy), num_matches, seed_base,
    ))


@mcp.tool()
def autocontext_validate_strategy_against_harness(
    scenario_name: str,
    strategy: str,
) -> str:
    """Validate a strategy against scenario constraints and harness validators.
    strategy should be a JSON string."""
    return json.dumps(tools.validate_strategy_against_harness(
        scenario_name, json.loads(strategy), ctx=_get_ctx(),
    ))


@mcp.tool()
def autocontext_publish_artifact(artifact_data: str) -> str:
    """Publish an artifact (harness, policy, or distilled model).
    artifact_data should be a JSON string of the artifact dict."""
    return json.dumps(tools.publish_artifact(
        _get_ctx(), json.loads(artifact_data),
    ))


@mcp.tool()
def autocontext_fetch_artifact(artifact_id: str) -> str:
    """Fetch a published artifact by its ID."""
    return json.dumps(tools.fetch_artifact(_get_ctx(), artifact_id))


@mcp.tool()
def autocontext_list_artifacts(
    scenario: str | None = None,
    artifact_type: str | None = None,
) -> str:
    """List published artifacts with optional scenario and type filters."""
    return json.dumps(tools.list_artifacts(
        _get_ctx(), scenario=scenario, artifact_type=artifact_type,
    ))


@mcp.tool()
def autocontext_distill_status(scenario: str | None = None) -> str:
    """Check status of distillation workflows, optionally filtered by scenario."""
    return json.dumps(tools.distill_status(_get_ctx(), scenario=scenario))


@mcp.tool()
def autocontext_trigger_distillation(
    scenario: str,
    source_artifact_ids: str | None = None,
    training_config: str | None = None,
) -> str:
    """Trigger a distillation workflow for a scenario.
    source_artifact_ids and training_config should be JSON strings."""
    ids = json.loads(source_artifact_ids) if source_artifact_ids else None
    config = json.loads(training_config) if training_config else None
    return json.dumps(tools.trigger_distillation(
        _get_ctx(), scenario, source_artifact_ids=ids, training_config=config,
    ))


@mcp.tool()
def autocontext_get_distill_job(job_id: str) -> str:
    """Get details of a specific distillation job by ID."""
    return json.dumps(tools.get_distill_job(_get_ctx(), job_id))


@mcp.tool()
def autocontext_update_distill_job(
    job_id: str,
    status: str,
    result_artifact_id: str | None = None,
    error_message: str | None = None,
    training_metrics: str | None = None,
) -> str:
    """Update a distillation job status. training_metrics should be a JSON string."""
    metrics = json.loads(training_metrics) if training_metrics else None
    return json.dumps(tools.update_distill_job(
        _get_ctx(), job_id, status,
        result_artifact_id=result_artifact_id,
        error_message=error_message,
        training_metrics=metrics,
    ))


@mcp.tool()
def autocontext_capabilities() -> str:
    """Return capability metadata for this MTS instance."""
    return json.dumps(tools.get_capabilities())


@mcp.tool()
def autocontext_export_package(scenario_name: str) -> str:
    """Export a versioned, portable strategy package for a scenario.

    Returns JSON with format_version, metadata, playbook, lessons, strategy, and harness.
    """
    return json.dumps(tools.export_package(_get_ctx(), scenario_name))


@mcp.tool()
def autocontext_import_package(package_data: str, conflict_policy: str = "merge") -> str:
    """Import a strategy package into scenario knowledge.

    package_data should be a JSON string of a StrategyPackage.
    conflict_policy: overwrite, merge, or skip.
    """
    return json.dumps(tools.import_package(
        _get_ctx(), json.loads(package_data), conflict_policy,
    ))

# -- Discovery & capability advertisement tools (AC-195) --


@mcp.tool()
def autocontext_skill_advertise() -> str:
    """Return full capability advertisement: version, runtime health, scenarios, artifact counts."""
    return json.dumps(tools.skill_advertise_capabilities(_get_ctx()))


@mcp.tool()
def autocontext_skill_scenario_capabilities(scenario_name: str) -> str:
    """Return per-scenario capabilities: evaluation mode, harness, playbook, best scores."""
    return json.dumps(tools.skill_scenario_capabilities(_get_ctx(), scenario_name))


@mcp.tool()
def autocontext_skill_runtime_health() -> str:
    """Return runtime health: executor mode, provider, harness mode, available models."""
    return json.dumps(tools.skill_runtime_health(_get_ctx()))


@mcp.tool()
def autocontext_skill_scenario_artifacts(scenario_name: str) -> str:
    """Return all artifacts associated with a scenario."""
    return json.dumps(tools.skill_scenario_artifact_lookup(_get_ctx(), scenario_name))


# -- ClawHub skill wrapper tools (AC-192) --


@mcp.tool()
def autocontext_skill_manifest() -> str:
    """Get the ClawHub skill manifest for this MTS instance.
    Returns name, version, capabilities, scenarios, and tool list."""
    return json.dumps(tools.skill_manifest(_get_ctx()))


@mcp.tool()
def autocontext_skill_discover(query: str | None = None) -> str:
    """Discover available scenarios, optionally filtered by natural language query."""
    return json.dumps(tools.skill_discover_scenarios(_get_ctx(), query))


@mcp.tool()
def autocontext_skill_select(description: str) -> str:
    """Recommend the best scenario for a problem description.
    Returns the top match with confidence score and alternatives."""
    return json.dumps(tools.skill_select_scenario(_get_ctx(), description))


@mcp.tool()
def autocontext_skill_evaluate(
    scenario_name: str, strategy: str, num_matches: int = 3, seed_base: int = 42,
) -> str:
    """Full validate + evaluate workflow for a strategy.
    strategy should be a JSON string. Returns validation + tournament results."""
    return json.dumps(tools.skill_evaluate(
        _get_ctx(), scenario_name, json.loads(strategy), num_matches, seed_base,
    ))


@mcp.tool()
def autocontext_skill_discover_artifacts(
    scenario: str | None = None, artifact_type: str | None = None,
) -> str:
    """Find published artifacts with optional scenario and type filters."""
    return json.dumps(tools.skill_discover_artifacts(
        _get_ctx(), scenario=scenario, artifact_type=artifact_type,
    ))


def run_server() -> None:
    """Synchronous entry point for the MCP server."""
    mcp.run(transport="stdio")
