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
        "mts_list_scenarios",
        "mts_describe_scenario",
        "mts_validate_strategy",
        "mts_run_match",
        "mts_run_tournament",
        "mts_read_playbook",
        "mts_read_trajectory",
        "mts_read_hints",
        "mts_read_skills",
    ]
    for name in expected:
        assert name in tool_names, f"Missing tool: {name}"


def test_tool_count() -> None:
    """At least 10 tools registered."""
    assert len(mcp._tool_manager._tools) >= 10


def test_list_scenarios_tool() -> None:
    """Tool invocation returns valid JSON."""
    from autocontext.mcp.server import mts_list_scenarios

    result = mts_list_scenarios()
    parsed = json.loads(result)
    assert isinstance(parsed, list)
    assert len(parsed) >= 2


def test_run_match_tool() -> None:
    """Tool invocation returns result with score."""
    from autocontext.mcp.server import mts_run_match

    strategy = json.dumps({"aggression": 0.5, "defense": 0.5, "path_bias": 0.5})
    result = mts_run_match(scenario_name="grid_ctf", strategy=strategy, seed=42)
    parsed = json.loads(result)
    assert "score" in parsed
    assert isinstance(parsed["score"], float)
