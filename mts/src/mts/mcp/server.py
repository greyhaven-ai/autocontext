"""MTS MCP server — exposes scenario, knowledge, and run tools via stdio."""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from mts.config import load_settings
from mts.mcp import tools
from mts.mcp.sandbox import SandboxManager

mcp = FastMCP("mts")
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
    from mts.knowledge.solver import SolveManager

    global _solve_mgr
    if _solve_mgr is None:
        _solve_mgr = SolveManager(_get_ctx().settings)
    return _solve_mgr


# -- Scenario exploration tools --


@mcp.tool()
def mts_list_scenarios() -> str:
    """List available game scenarios with rules preview."""
    return json.dumps(tools.list_scenarios())


@mcp.tool()
def mts_describe_scenario(scenario_name: str) -> str:
    """Get full scenario description: rules, strategy interface, evaluation criteria."""
    return json.dumps(tools.describe_scenario(scenario_name))


@mcp.tool()
def mts_validate_strategy(scenario_name: str, strategy: str) -> str:
    """Validate a strategy JSON string against scenario constraints."""
    return json.dumps(tools.validate_strategy(scenario_name, json.loads(strategy)))


@mcp.tool()
def mts_run_match(scenario_name: str, strategy: str, seed: int = 42) -> str:
    """Execute a single match and return the result."""
    return json.dumps(tools.run_match(scenario_name, json.loads(strategy), seed))


@mcp.tool()
def mts_run_tournament(scenario_name: str, strategy: str, matches: int = 3, seed_base: int = 1000) -> str:
    """Run N matches and return aggregate stats."""
    return json.dumps(tools.run_tournament(scenario_name, json.loads(strategy), matches, seed_base))


# -- Knowledge reading tools --


@mcp.tool()
def mts_read_playbook(scenario_name: str) -> str:
    """Read current strategy playbook for a scenario."""
    return tools.read_playbook(_get_ctx(), scenario_name)


@mcp.tool()
def mts_read_trajectory(run_id: str) -> str:
    """Read score trajectory table for a run."""
    return tools.read_trajectory(_get_ctx(), run_id)


@mcp.tool()
def mts_read_analysis(scenario_name: str, generation: int) -> str:
    """Read analysis for a specific generation."""
    return tools.read_analysis(_get_ctx(), scenario_name, generation)


@mcp.tool()
def mts_read_hints(scenario_name: str) -> str:
    """Read persisted coach hints for a scenario."""
    return tools.read_hints(_get_ctx(), scenario_name)


@mcp.tool()
def mts_read_tools(scenario_name: str) -> str:
    """Read architect-generated tools for a scenario."""
    return tools.read_tool_context(_get_ctx(), scenario_name)


@mcp.tool()
def mts_read_skills(scenario_name: str) -> str:
    """Read operational lessons from SKILL.md for a scenario."""
    return tools.read_skills(_get_ctx(), scenario_name)


# -- Run management tools --


@mcp.tool()
def mts_list_runs() -> str:
    """List recent runs."""
    return json.dumps(tools.list_runs(_get_ctx()))


@mcp.tool()
def mts_run_status(run_id: str) -> str:
    """Get generation-level metrics for a run."""
    return json.dumps(tools.run_status(_get_ctx(), run_id))


@mcp.tool()
def mts_run_replay(run_id: str, generation: int) -> str:
    """Read replay JSON for a specific generation."""
    return json.dumps(tools.run_replay(_get_ctx(), run_id, generation))


# -- Sandbox tools --


@mcp.tool()
def mts_sandbox_create(scenario_name: str, user_id: str = "anonymous") -> str:
    """Create an isolated sandbox for external play."""
    mgr = _get_sandbox_mgr()
    sandbox = mgr.create(scenario_name, user_id)
    return json.dumps({"sandbox_id": sandbox.sandbox_id, "scenario_name": sandbox.scenario_name, "user_id": sandbox.user_id})


@mcp.tool()
def mts_sandbox_run(sandbox_id: str, generations: int = 1) -> str:
    """Run generation(s) in a sandbox."""
    mgr = _get_sandbox_mgr()
    result = mgr.run_generation(sandbox_id, generations)
    return json.dumps(result)


@mcp.tool()
def mts_sandbox_status(sandbox_id: str) -> str:
    """Get sandbox status."""
    mgr = _get_sandbox_mgr()
    return json.dumps(mgr.get_status(sandbox_id))


@mcp.tool()
def mts_sandbox_playbook(sandbox_id: str) -> str:
    """Read sandbox playbook."""
    mgr = _get_sandbox_mgr()
    return mgr.read_playbook(sandbox_id)


@mcp.tool()
def mts_sandbox_list() -> str:
    """List active sandboxes."""
    mgr = _get_sandbox_mgr()
    return json.dumps(mgr.list_sandboxes())


@mcp.tool()
def mts_sandbox_destroy(sandbox_id: str) -> str:
    """Destroy a sandbox and clean up its data."""
    mgr = _get_sandbox_mgr()
    destroyed = mgr.destroy(sandbox_id)
    return json.dumps({"destroyed": destroyed, "sandbox_id": sandbox_id})


# -- Knowledge API tools --


@mcp.tool()
def mts_export_skill(scenario_name: str) -> str:
    """Export a portable skill package (playbook + lessons + strategy) for a solved scenario.
    Returns markdown + JSON that can be dropped into any agent's skill directory."""
    return json.dumps(tools.export_skill(_get_ctx(), scenario_name))


@mcp.tool()
def mts_list_solved() -> str:
    """List all scenarios with solved strategies, including best scores and run counts."""
    return json.dumps(tools.list_solved(_get_ctx()))


@mcp.tool()
def mts_search_strategies(query: str, top_k: int = 5) -> str:
    """Search solved scenarios by natural language query.
    Returns ranked results with relevance scores."""
    return json.dumps(tools.search_strategies(_get_ctx(), query, top_k))


@mcp.tool()
def mts_solve_scenario(description: str, generations: int = 5) -> str:
    """Submit a new problem for on-demand solving. MTS creates a scenario from the
    description, runs strategy evolution, and produces a skill package.
    Returns a job_id for polling status."""
    from mts.knowledge.solver import SolveManager

    mgr: SolveManager = _get_solve_mgr()  # type: ignore[assignment]
    job_id = mgr.submit(description, generations)
    return json.dumps({"job_id": job_id, "status": "pending"})


@mcp.tool()
def mts_solve_status(job_id: str) -> str:
    """Check status of a solve-on-demand job."""
    from mts.knowledge.solver import SolveManager

    mgr: SolveManager = _get_solve_mgr()  # type: ignore[assignment]
    return json.dumps(mgr.get_status(job_id))


@mcp.tool()
def mts_solve_result(job_id: str) -> str:
    """Get the skill package result of a completed solve job."""
    from mts.knowledge.solver import SolveManager

    mgr: SolveManager = _get_solve_mgr()  # type: ignore[assignment]
    pkg = mgr.get_result(job_id)
    if pkg is None:
        return json.dumps({"error": "Job not completed or not found"})
    return json.dumps(pkg.to_dict())


# -- Human feedback tools --


@mcp.tool()
def mts_record_feedback(
    scenario_name: str,
    agent_output: str,
    human_score: float | None = None,
    human_notes: str = "",
    generation_id: str | None = None,
) -> str:
    """Record human feedback on an agent task output. Score should be 0.0–1.0."""
    return json.dumps(tools.record_feedback(
        _get_ctx(), scenario_name, agent_output, human_score, human_notes, generation_id
    ))


@mcp.tool()
def mts_get_feedback(scenario_name: str, limit: int = 10) -> str:
    """Get recent human feedback for a scenario."""
    return json.dumps(tools.get_feedback(_get_ctx(), scenario_name, limit))


@mcp.tool()
def mts_run_improvement_loop(
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
def mts_create_agent_task(
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
def mts_list_agent_tasks() -> str:
    """List all saved agent task specs."""
    return json.dumps(tools.list_agent_tasks(_get_ctx()))


@mcp.tool()
def mts_get_agent_task(name: str) -> str:
    """Get full agent task spec by name."""
    return json.dumps(tools.get_agent_task(_get_ctx(), name))


@mcp.tool()
def mts_delete_agent_task(name: str) -> str:
    """Delete an agent task spec."""
    return json.dumps(tools.delete_agent_task(_get_ctx(), name))


@mcp.tool()
def mts_evaluate_output(task_name: str, output: str) -> str:
    """One-shot evaluation of an output against a saved agent task spec.
    Returns score, reasoning, and dimension scores."""
    return json.dumps(tools.evaluate_output(_get_ctx(), task_name, output))


# -- Task Queue tools --


@mcp.tool()
def mts_queue_improvement_run(
    task_name: str,
    initial_output: str | None = None,
    priority: int = 0,
) -> str:
    """Queue an agent task for background improvement loop processing.
    The task runner daemon will pick it up automatically."""
    return json.dumps(tools.queue_improvement_run(_get_ctx(), task_name, initial_output, priority))


@mcp.tool()
def mts_get_queue_status() -> str:
    """Get task queue status: pending, running, recent completed/failed."""
    return json.dumps(tools.get_queue_status(_get_ctx()))


@mcp.tool()
def mts_get_task_result(task_id: str) -> str:
    """Get the result of a specific queued task by ID."""
    return json.dumps(tools.get_task_result(_get_ctx(), task_id))


@mcp.tool()
def mts_get_best_output(task_name: str) -> str:
    """Get the highest-scoring output for a task across all completed runs."""
    return json.dumps(tools.get_best_output(_get_ctx(), task_name))


def run_server() -> None:
    """Synchronous entry point for the MCP server."""
    mcp.run(transport="stdio")
