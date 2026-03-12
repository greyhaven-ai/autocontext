"""Tests for analysis injection into prompts."""
from __future__ import annotations

from pathlib import Path

from autocontext.prompts.templates import build_prompt_bundle
from autocontext.scenarios.base import Observation
from autocontext.storage import ArtifactStore


def test_latest_analysis_injected_gen2(tmp_path: Path) -> None:
    store = ArtifactStore(
        tmp_path / "runs", tmp_path / "knowledge", tmp_path / "skills", tmp_path / ".claude/skills"
    )
    # Write gen 1 analysis
    analysis_dir = tmp_path / "knowledge" / "grid_ctf" / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    (analysis_dir / "gen_1.md").write_text("Gen 1 analysis content\n", encoding="utf-8")
    result = store.read_latest_advance_analysis("grid_ctf", 2)
    assert "Gen 1 analysis" in result


def test_no_analysis_for_gen1(tmp_path: Path) -> None:
    store = ArtifactStore(
        tmp_path / "runs", tmp_path / "knowledge", tmp_path / "skills", tmp_path / ".claude/skills"
    )
    result = store.read_latest_advance_analysis("grid_ctf", 1)
    assert result == ""


def test_analysis_picks_highest_gen(tmp_path: Path) -> None:
    store = ArtifactStore(
        tmp_path / "runs", tmp_path / "knowledge", tmp_path / "skills", tmp_path / ".claude/skills"
    )
    analysis_dir = tmp_path / "knowledge" / "grid_ctf" / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    (analysis_dir / "gen_1.md").write_text("Gen 1\n", encoding="utf-8")
    (analysis_dir / "gen_2.md").write_text("Gen 2\n", encoding="utf-8")
    (analysis_dir / "gen_3.md").write_text("Gen 3\n", encoding="utf-8")
    result = store.read_latest_advance_analysis("grid_ctf", 4)
    assert "Gen 3" in result


def test_analysis_in_prompt_bundle(tmp_path: Path) -> None:
    prompts = build_prompt_bundle(
        scenario_rules="rules",
        strategy_interface="{}",
        evaluation_criteria="criteria",
        previous_summary="best: 0.5",
        observation=Observation(narrative="n", state={}, constraints=[]),
        current_playbook="playbook",
        available_tools="tools",
        recent_analysis="This is the analysis from last gen",
    )
    assert "Most recent generation analysis" in prompts.competitor
    assert "This is the analysis from last gen" in prompts.analyst


def test_analysis_suppressed_by_ablation(tmp_path: Path) -> None:
    """When ablation_no_feedback is True, no analysis should be injected."""
    # With ablation, the runner sets recent_analysis to ""
    prompts = build_prompt_bundle(
        scenario_rules="rules",
        strategy_interface="{}",
        evaluation_criteria="criteria",
        previous_summary="best: 0.5",
        observation=Observation(narrative="n", state={}, constraints=[]),
        current_playbook="playbook",
        available_tools="tools",
        recent_analysis="",  # ablation suppresses this
    )
    assert "Most recent generation analysis" not in prompts.competitor
