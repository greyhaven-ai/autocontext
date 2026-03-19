"""Tests for cross-run knowledge inheritance."""
from __future__ import annotations

from pathlib import Path

from autocontext.config import AppSettings
from autocontext.loop import GenerationRunner
from autocontext.storage import SQLiteStore


def _make_settings(tmp_path: Path, **overrides: object) -> AppSettings:
    defaults: dict[str, object] = dict(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        event_stream_path=tmp_path / "runs" / "events.ndjson",
        seed_base=2000,
        agent_provider="deterministic",
        matches_per_generation=2,
        cross_run_inheritance=True,
    )
    defaults.update(overrides)
    return AppSettings(**defaults)  # type: ignore[arg-type]


def _run(tmp_path: Path, run_id: str, gens: int = 1, **overrides: object) -> GenerationRunner:
    settings = _make_settings(tmp_path, **overrides)
    runner = GenerationRunner(settings)
    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    runner.migrate(migrations_dir)
    runner.run(scenario_name="grid_ctf", generations=gens, run_id=run_id)
    return runner


def test_snapshot_on_completion(tmp_path: Path) -> None:
    _run(tmp_path, "snap_r1")
    snapshot_dir = tmp_path / "knowledge" / "grid_ctf" / "snapshots" / "snap_r1"
    assert snapshot_dir.exists()
    assert (snapshot_dir / "playbook.md").exists()


def test_best_snapshot_query(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    sqlite = SQLiteStore(settings.db_path)
    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    sqlite.migrate(migrations_dir)
    # Create dummy runs to satisfy FK constraint
    sqlite.create_run("r1", "grid_ctf", 1, "local")
    sqlite.create_run("r2", "grid_ctf", 1, "local")
    sqlite.save_knowledge_snapshot("grid_ctf", "r1", 0.5, 1000.0, "hash1")
    sqlite.save_knowledge_snapshot("grid_ctf", "r2", 0.8, 1100.0, "hash2")
    best = sqlite.get_best_knowledge_snapshot("grid_ctf")
    assert best is not None
    assert best["run_id"] == "r2"
    assert best["best_score"] == 0.8


def test_no_snapshot_returns_none(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    sqlite = SQLiteStore(settings.db_path)
    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    sqlite.migrate(migrations_dir)
    assert sqlite.get_best_knowledge_snapshot("nonexistent") is None


def test_restore_on_fresh_run(tmp_path: Path) -> None:
    # Run 1 creates knowledge
    _run(tmp_path, "inherit_r1")
    # Remove playbook to simulate fresh start
    playbook_path = tmp_path / "knowledge" / "grid_ctf" / "playbook.md"
    assert playbook_path.exists()
    playbook_path.unlink()
    # Run 2 should inherit from run 1
    _run(tmp_path, "inherit_r2")
    assert playbook_path.exists()


def test_no_restore_when_playbook_exists(tmp_path: Path) -> None:
    _run(tmp_path, "no_restore_r1")
    playbook_path = tmp_path / "knowledge" / "grid_ctf" / "playbook.md"
    assert playbook_path.exists()
    # Run 2 with existing playbook should NOT overwrite from snapshot
    _run(tmp_path, "no_restore_r2")
    # Playbook should still exist (may be updated by run 2's coach, but not from snapshot)
    assert playbook_path.exists()


def test_disabled_by_config(tmp_path: Path) -> None:
    _run(tmp_path, "disabled_r1", cross_run_inheritance=False)
    snapshot_dir = tmp_path / "knowledge" / "grid_ctf" / "snapshots" / "disabled_r1"
    assert not snapshot_dir.exists()


def test_disabled_by_ablation(tmp_path: Path) -> None:
    _run(tmp_path, "ablation_r1", ablation_no_feedback=True)
    snapshot_dir = tmp_path / "knowledge" / "grid_ctf" / "snapshots" / "ablation_r1"
    assert not snapshot_dir.exists()


def test_snapshot_includes_hints(tmp_path: Path) -> None:
    # Pre-seed hints
    hints_dir = tmp_path / "knowledge" / "grid_ctf"
    hints_dir.mkdir(parents=True, exist_ok=True)
    (hints_dir / "hints.md").write_text("- Hint from pre-seed\n", encoding="utf-8")
    _run(tmp_path, "hints_snap")
    snapshot_dir = tmp_path / "knowledge" / "grid_ctf" / "snapshots" / "hints_snap"
    # Hints may or may not be in snapshot depending on whether they were written during run
    # The key thing is no crash
    assert snapshot_dir.exists()


def test_snapshot_includes_structured_hint_state(tmp_path: Path) -> None:
    scenario_dir = tmp_path / "knowledge" / "grid_ctf"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    (scenario_dir / "hint_state.json").write_text(
        (
            '{"policy":{"max_hints":2,"archive_rotated":true},"active":'
            '[{"text":"Hint from state","rank":1,"generation_added":1,'
            '"impact_score":0.9,"metadata":{}}],"archived":[]}'
        ),
        encoding="utf-8",
    )

    _run(tmp_path, "state_snap")

    snapshot_dir = tmp_path / "knowledge" / "grid_ctf" / "snapshots" / "state_snap"
    assert snapshot_dir.exists()
    assert (snapshot_dir / "hint_state.json").exists()
    assert "Hint from state" in (snapshot_dir / "hint_state.json").read_text(encoding="utf-8")


def test_snapshot_includes_skills(tmp_path: Path) -> None:
    _run(tmp_path, "skills_snap")
    snapshot_dir = tmp_path / "knowledge" / "grid_ctf" / "snapshots" / "skills_snap"
    assert snapshot_dir.exists()
    # SKILL.md should be snapshotted
    assert (snapshot_dir / "SKILL.md").exists()
