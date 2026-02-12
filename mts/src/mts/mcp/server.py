"""MTS MCP server — exposes scenario, knowledge, and run tools via stdio."""

from __future__ import annotations

import asyncio
import json

from mcp.server import Server  # type: ignore[import-not-found]
from mcp.server.stdio import stdio_server  # type: ignore[import-not-found]

from mts.config import load_settings
from mts.mcp import tools
from mts.mcp.sandbox import SandboxManager

server = Server("mts")
_ctx: tools.MtsToolContext | None = None
_sandbox_mgr: SandboxManager | None = None


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


# -- Scenario exploration tools --


@server.tool()
async def mts_list_scenarios() -> str:
    """List available game scenarios with rules preview."""
    return json.dumps(tools.list_scenarios())


@server.tool()
async def mts_describe_scenario(scenario_name: str) -> str:
    """Get full scenario description: rules, strategy interface, evaluation criteria."""
    return json.dumps(tools.describe_scenario(scenario_name))


@server.tool()
async def mts_validate_strategy(scenario_name: str, strategy: str) -> str:
    """Validate a strategy JSON string against scenario constraints."""
    return json.dumps(tools.validate_strategy(scenario_name, json.loads(strategy)))


@server.tool()
async def mts_run_match(scenario_name: str, strategy: str, seed: int = 42) -> str:
    """Execute a single match and return the result."""
    return json.dumps(tools.run_match(scenario_name, json.loads(strategy), seed))


@server.tool()
async def mts_run_tournament(scenario_name: str, strategy: str, matches: int = 3, seed_base: int = 1000) -> str:
    """Run N matches and return aggregate stats."""
    return json.dumps(tools.run_tournament(scenario_name, json.loads(strategy), matches, seed_base))


# -- Knowledge reading tools --


@server.tool()
async def mts_read_playbook(scenario_name: str) -> str:
    """Read current strategy playbook for a scenario."""
    return tools.read_playbook(_get_ctx(), scenario_name)


@server.tool()
async def mts_read_trajectory(run_id: str) -> str:
    """Read score trajectory table for a run."""
    return tools.read_trajectory(_get_ctx(), run_id)


@server.tool()
async def mts_read_analysis(scenario_name: str, generation: int) -> str:
    """Read analysis for a specific generation."""
    return tools.read_analysis(_get_ctx(), scenario_name, generation)


@server.tool()
async def mts_read_hints(scenario_name: str) -> str:
    """Read persisted coach hints for a scenario."""
    return tools.read_hints(_get_ctx(), scenario_name)


@server.tool()
async def mts_read_tools(scenario_name: str) -> str:
    """Read architect-generated tools for a scenario."""
    return tools.read_tool_context(_get_ctx(), scenario_name)


@server.tool()
async def mts_read_skills(scenario_name: str) -> str:
    """Read operational lessons from SKILL.md for a scenario."""
    return tools.read_skills(_get_ctx(), scenario_name)


# -- Run management tools --


@server.tool()
async def mts_list_runs() -> str:
    """List recent runs."""
    return json.dumps(tools.list_runs(_get_ctx()))


@server.tool()
async def mts_run_status(run_id: str) -> str:
    """Get generation-level metrics for a run."""
    return json.dumps(tools.run_status(_get_ctx(), run_id))


@server.tool()
async def mts_run_replay(run_id: str, generation: int) -> str:
    """Read replay JSON for a specific generation."""
    return json.dumps(tools.run_replay(_get_ctx(), run_id, generation))


# -- Sandbox tools --


@server.tool()
async def mts_sandbox_create(scenario_name: str, user_id: str = "anonymous") -> str:
    """Create an isolated sandbox for external play."""
    mgr = _get_sandbox_mgr()
    sandbox = mgr.create(scenario_name, user_id)
    return json.dumps({"sandbox_id": sandbox.sandbox_id, "scenario_name": sandbox.scenario_name, "user_id": sandbox.user_id})


@server.tool()
async def mts_sandbox_run(sandbox_id: str, generations: int = 1) -> str:
    """Run generation(s) in a sandbox."""
    mgr = _get_sandbox_mgr()
    result = mgr.run_generation(sandbox_id, generations)
    return json.dumps(result)


@server.tool()
async def mts_sandbox_status(sandbox_id: str) -> str:
    """Get sandbox status."""
    mgr = _get_sandbox_mgr()
    return json.dumps(mgr.get_status(sandbox_id))


@server.tool()
async def mts_sandbox_playbook(sandbox_id: str) -> str:
    """Read sandbox playbook."""
    mgr = _get_sandbox_mgr()
    return mgr.read_playbook(sandbox_id)


@server.tool()
async def mts_sandbox_list() -> str:
    """List active sandboxes."""
    mgr = _get_sandbox_mgr()
    return json.dumps(mgr.list_sandboxes())


@server.tool()
async def mts_sandbox_destroy(sandbox_id: str) -> str:
    """Destroy a sandbox and clean up its data."""
    mgr = _get_sandbox_mgr()
    destroyed = mgr.destroy(sandbox_id)
    return json.dumps({"destroyed": destroyed, "sandbox_id": sandbox_id})


async def main() -> None:
    """Run the MTS MCP server on stdio."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def run_server() -> None:
    """Synchronous entry point for the MCP server."""
    asyncio.run(main())
