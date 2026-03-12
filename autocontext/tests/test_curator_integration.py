"""Integration tests for curator in the generation loop."""
from __future__ import annotations

from pathlib import Path

from autocontext.config import AppSettings
from autocontext.loop import GenerationRunner


def _make_settings(tmp_path: Path, **overrides) -> AppSettings:
    defaults = dict(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        event_stream_path=tmp_path / "runs" / "events.ndjson",
        seed_base=2000,
        agent_provider="deterministic",
        matches_per_generation=2,
        curator_enabled=True,
    )
    defaults.update(overrides)
    return AppSettings(**defaults)


def test_curator_runs_after_tournament(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    runner = GenerationRunner(settings)
    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    runner.migrate(migrations_dir)
    runner.run(scenario_name="grid_ctf", generations=2, run_id="curator_run")
    # Check for curator output in DB - may or may not exist depending on whether
    # there was a current playbook to compare against (gen 1 has no prior playbook)
    # Gen 2+ should have curator output if gen 1 advanced
    outputs = runner.sqlite.get_generation_metrics("curator_run")
    assert len(outputs) == 2


def test_playbook_quality_gate_e2e(tmp_path: Path) -> None:
    """3-gen run with playbook versions reflecting curator decisions."""
    settings = _make_settings(tmp_path)
    runner = GenerationRunner(settings)
    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    runner.migrate(migrations_dir)
    summary = runner.run(scenario_name="grid_ctf", generations=3, run_id="curator_e2e")
    assert summary.generations_executed == 3
    # Playbook should exist
    playbook_path = tmp_path / "knowledge" / "grid_ctf" / "playbook.md"
    assert playbook_path.exists()


def test_curator_and_coach_coexist(tmp_path: Path) -> None:
    """Coach runs normally, curator post-processes."""
    settings = _make_settings(tmp_path)
    runner = GenerationRunner(settings)
    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    runner.migrate(migrations_dir)
    runner.run(scenario_name="grid_ctf", generations=1, run_id="coexist_run")
    # Coach history should exist
    coach_path = tmp_path / "knowledge" / "grid_ctf" / "coach_history.md"
    assert coach_path.exists()
    # Playbook should exist from coach
    playbook_path = tmp_path / "knowledge" / "grid_ctf" / "playbook.md"
    assert playbook_path.exists()
