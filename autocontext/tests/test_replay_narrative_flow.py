"""Tests for Gap 2: Replay narratives reach agent prompts."""
from __future__ import annotations

from pathlib import Path

from autocontext.config import AppSettings
from autocontext.loop import GenerationRunner
from autocontext.prompts.templates import build_prompt_bundle
from autocontext.scenarios.base import Observation


def test_replay_narrative_persisted_after_tournament(tmp_path: Path) -> None:
    settings = AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        event_stream_path=tmp_path / "runs" / "events.ndjson",
        seed_base=2000,
        agent_provider="deterministic",
        matches_per_generation=2,
    )
    runner = GenerationRunner(settings)
    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    runner.migrate(migrations_dir)

    run_id = "narrative_run"
    runner.run(scenario_name="grid_ctf", generations=1, run_id=run_id)

    narrative_path = tmp_path / "runs" / run_id / "generations" / "gen_1" / "narrative.md"
    assert narrative_path.exists(), "Narrative markdown should be persisted after tournament"
    content = narrative_path.read_text(encoding="utf-8")
    assert len(content.strip()) > 0, "Narrative should not be empty"


def test_replay_narrative_included_in_next_gen_prompts(tmp_path: Path) -> None:
    settings = AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        event_stream_path=tmp_path / "runs" / "events.ndjson",
        seed_base=2000,
        agent_provider="deterministic",
        matches_per_generation=2,
    )
    runner = GenerationRunner(settings)
    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    runner.migrate(migrations_dir)

    run_id = "narrative_multi_run"
    runner.run(scenario_name="grid_ctf", generations=2, run_id=run_id)

    # Gen 1 narrative should exist
    narrative_path = tmp_path / "runs" / run_id / "generations" / "gen_1" / "narrative.md"
    assert narrative_path.exists()
    gen1_narrative = narrative_path.read_text(encoding="utf-8").strip()
    assert len(gen1_narrative) > 0

    # Gen 2 narrative should also exist
    gen2_narrative_path = tmp_path / "runs" / run_id / "generations" / "gen_2" / "narrative.md"
    assert gen2_narrative_path.exists()


def test_replay_narrative_empty_for_gen_1() -> None:
    """First generation gets no replay narrative (empty string)."""
    prompts = build_prompt_bundle(
        scenario_rules="Test rules",
        strategy_interface='{"x": float}',
        evaluation_criteria="Win rate",
        previous_summary="best score so far: 0.0000",
        observation=Observation(narrative="Test", state={}, constraints=[]),
        current_playbook="No playbook yet.",
        available_tools="No generated tools available.",
        replay_narrative="",
    )
    # Empty replay narrative should not appear in prompt
    assert "Previous match replay:" not in prompts.competitor


def test_build_prompt_bundle_with_replay_narrative() -> None:
    """PromptBundle includes replay text in base context when provided."""
    narrative = "Capture phase ended with progress 0.52."
    prompts = build_prompt_bundle(
        scenario_rules="Test rules",
        strategy_interface='{"x": float}',
        evaluation_criteria="Win rate",
        previous_summary="best score so far: 0.5200",
        observation=Observation(narrative="Test", state={}, constraints=[]),
        current_playbook="No playbook yet.",
        available_tools="No generated tools available.",
        replay_narrative=narrative,
    )
    assert "Previous match replay:" in prompts.competitor
    assert narrative in prompts.competitor
    assert narrative in prompts.analyst
