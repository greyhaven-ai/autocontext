"""Tests for MCP server tool registration."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("mcp", reason="MCP not installed")

from autocontext.mcp.server import mcp  # noqa: E402


def test_server_has_tools() -> None:
    """Server registers expected tool names."""
    tool_names = list(mcp._tool_manager._tools.keys())
    expected = [
        "autocontext_list_scenarios",
        "autocontext_describe_scenario",
        "autocontext_validate_strategy",
        "autocontext_run_match",
        "autocontext_run_tournament",
        "autocontext_read_playbook",
        "autocontext_read_trajectory",
        "autocontext_read_hints",
        "autocontext_read_skills",
    ]
    for name in expected:
        assert name in tool_names, f"Missing tool: {name}"


def test_tool_count() -> None:
    """At least 10 tools registered."""
    assert len(mcp._tool_manager._tools) >= 10


def test_list_scenarios_tool() -> None:
    """Tool invocation returns valid JSON."""
    from autocontext.mcp.server import autocontext_list_scenarios

    result = autocontext_list_scenarios()
    parsed = json.loads(result)
    assert isinstance(parsed, list)
    assert len(parsed) >= 2


def test_run_match_tool() -> None:
    """Tool invocation returns result with score."""
    from autocontext.mcp.server import autocontext_run_match

    strategy = json.dumps({"aggression": 0.5, "defense": 0.5, "path_bias": 0.5})
    result = autocontext_run_match(scenario_name="grid_ctf", strategy=strategy, seed=42)
    parsed = json.loads(result)
    assert "score" in parsed
    assert isinstance(parsed["score"], float)


def test_evidence_artifact_tool_function_exists() -> None:
    from autocontext.mcp import server

    assert hasattr(server, "autocontext_evidence_artifact")


def test_solve_tools_expose_prefixed_and_unprefixed_names() -> None:
    from autocontext.mcp import server

    tool_names = set(mcp._tool_manager._tools.keys())
    for name in [
        "autocontext_solve_scenario",
        "autocontext_solve_status",
        "autocontext_solve_result",
        "solve_scenario",
        "solve_status",
        "solve_result",
    ]:
        assert name in tool_names
        assert hasattr(server, name)
