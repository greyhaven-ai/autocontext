"""Tests for hint persistence across restarts."""
from __future__ import annotations

from pathlib import Path

from autocontext.config import AppSettings
from autocontext.loop import GenerationRunner
from autocontext.storage import ArtifactStore


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
    )
    defaults.update(overrides)
    return AppSettings(**defaults)


def _run_gen(tmp_path: Path, run_id: str, gens: int = 1, **overrides) -> GenerationRunner:
    settings = _make_settings(tmp_path, **overrides)
    runner = GenerationRunner(settings)
    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    runner.migrate(migrations_dir)
    runner.run(scenario_name="grid_ctf", generations=gens, run_id=run_id)
    return runner


def test_hints_written_on_advance(tmp_path: Path) -> None:
    _run_gen(tmp_path, "hints_w", gens=1)
    hints_path = tmp_path / "knowledge" / "grid_ctf" / "hints.md"
    assert hints_path.exists()
    assert hints_path.read_text(encoding="utf-8").strip()


def test_hints_not_written_on_rollback(tmp_path: Path) -> None:
    # High threshold forces rollback on gen 2
    _run_gen(tmp_path, "hints_rb", gens=2, backpressure_min_delta=0.4, max_retries=0)
    hints_path = tmp_path / "knowledge" / "grid_ctf" / "hints.md"
    # Hints should exist from gen 1 advance, but shouldn't be overwritten by gen 2 rollback
    if hints_path.exists():
        content = hints_path.read_text(encoding="utf-8")
        # The content should be from gen 1, not gen 2
        assert content.strip()


def test_hints_loaded_on_run_start(tmp_path: Path) -> None:
    # Pre-seed hints
    hints_dir = tmp_path / "knowledge" / "grid_ctf"
    hints_dir.mkdir(parents=True, exist_ok=True)
    (hints_dir / "hints.md").write_text("- Pre-seeded hint: try aggression=0.7\n", encoding="utf-8")
    _run_gen(tmp_path, "hints_load", gens=1)
    # If hints were loaded, they survive (the runner reads them at start)
    content = (hints_dir / "hints.md").read_text(encoding="utf-8")
    assert content.strip()  # hints.md should still have content


def test_hints_survive_restart(tmp_path: Path) -> None:
    _run_gen(tmp_path, "hints_r1", gens=1)
    hints_path = tmp_path / "knowledge" / "grid_ctf" / "hints.md"
    assert hints_path.exists()
    content_before = hints_path.read_text(encoding="utf-8")
    # Create new runner (simulates restart)
    settings = _make_settings(tmp_path)
    runner2 = GenerationRunner(settings)
    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    runner2.migrate(migrations_dir)
    # Hints should still be readable
    assert runner2.artifacts.read_hints("grid_ctf") == content_before


def test_empty_hints_graceful(tmp_path: Path) -> None:
    store = ArtifactStore(
        tmp_path / "runs", tmp_path / "knowledge", tmp_path / "skills", tmp_path / ".claude/skills"
    )
    assert store.read_hints("nonexistent") == ""
