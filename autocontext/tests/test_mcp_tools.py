"""Tests for MCP tool implementation functions."""

from __future__ import annotations

from pathlib import Path

import pytest

from autocontext.config import AppSettings
from autocontext.mcp.tools import (
    MtsToolContext,
    describe_scenario,
    list_scenarios,
    read_hints,
    read_playbook,
    read_skills,
    run_match,
    run_tournament,
    validate_strategy,
)


def test_list_scenarios() -> None:
    scenarios = list_scenarios()
    names = [s["name"] for s in scenarios]
    assert "grid_ctf" in names
    assert "othello" in names
    for s in scenarios:
        assert "rules_preview" in s
        assert len(s["rules_preview"]) <= 200


def test_describe_grid_ctf() -> None:
    desc = describe_scenario("grid_ctf")
    assert "rules" in desc
    assert "strategy_interface" in desc
    assert "evaluation_criteria" in desc
    assert len(desc["rules"]) > 0


def test_describe_unknown_scenario() -> None:
    with pytest.raises(KeyError):
        describe_scenario("nonexistent_scenario")


def test_validate_valid_strategy() -> None:
    result = validate_strategy("grid_ctf", {"aggression": 0.5, "defense": 0.5, "path_bias": 0.5})
    assert result["valid"] is True


def test_validate_invalid_strategy() -> None:
    result = validate_strategy("grid_ctf", {"aggression": 5.0, "defense": 0.5, "path_bias": 0.5})
    assert result["valid"] is False
    assert result["reason"] != ""


def test_run_match_returns_result() -> None:
    result = run_match("grid_ctf", {"aggression": 0.5, "defense": 0.5, "path_bias": 0.5}, seed=42)
    assert "score" in result
    assert "winner" in result
    assert "summary" in result
    assert "replay" in result
    assert "metrics" in result


def test_run_match_deterministic_seed() -> None:
    r1 = run_match("grid_ctf", {"aggression": 0.5, "defense": 0.5, "path_bias": 0.5}, seed=42)
    r2 = run_match("grid_ctf", {"aggression": 0.5, "defense": 0.5, "path_bias": 0.5}, seed=42)
    assert r1["score"] == r2["score"]


def test_run_tournament_aggregate() -> None:
    result = run_tournament("grid_ctf", {"aggression": 0.5, "defense": 0.5, "path_bias": 0.5}, matches=3, seed_base=1000)
    assert result["matches"] == 3
    assert len(result["scores"]) == 3
    assert result["mean_score"] == pytest.approx(sum(result["scores"]) / 3)
    assert result["best_score"] == max(result["scores"])


def test_read_playbook_empty(tmp_path: Path) -> None:
    settings = AppSettings(
        knowledge_root=tmp_path / "knowledge",
        runs_root=tmp_path / "runs",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        db_path=tmp_path / "test.sqlite3",
    )
    ctx = MtsToolContext(settings)
    result = read_playbook(ctx, "grid_ctf")
    assert "No playbook yet" in result


def test_read_playbook_with_content(tmp_path: Path) -> None:
    settings = AppSettings(
        knowledge_root=tmp_path / "knowledge",
        runs_root=tmp_path / "runs",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        db_path=tmp_path / "test.sqlite3",
    )
    ctx = MtsToolContext(settings)
    playbook_dir = tmp_path / "knowledge" / "grid_ctf"
    playbook_dir.mkdir(parents=True)
    (playbook_dir / "playbook.md").write_text("# Test Playbook\nSome content.", encoding="utf-8")
    result = read_playbook(ctx, "grid_ctf")
    assert "Test Playbook" in result


def test_read_hints_empty(tmp_path: Path) -> None:
    settings = AppSettings(
        knowledge_root=tmp_path / "knowledge",
        runs_root=tmp_path / "runs",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        db_path=tmp_path / "test.sqlite3",
    )
    ctx = MtsToolContext(settings)
    result = read_hints(ctx, "grid_ctf")
    assert result == ""


def test_read_skills_empty(tmp_path: Path) -> None:
    settings = AppSettings(
        knowledge_root=tmp_path / "knowledge",
        runs_root=tmp_path / "runs",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        db_path=tmp_path / "test.sqlite3",
    )
    ctx = MtsToolContext(settings)
    result = read_skills(ctx, "grid_ctf")
    assert result == ""
