"""Tests for buffered artifact integration (MTS-24)."""
from __future__ import annotations

from pathlib import Path

from mts.storage.artifacts import ArtifactStore


def test_persist_generation_creates_files_with_buffer(tmp_path: Path) -> None:
    """persist_generation writes all expected files via buffer."""
    store = ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        enable_buffered_writes=True,
    )
    store.persist_generation(
        run_id="run_1",
        generation_index=1,
        metrics={"score": 0.5},
        replay_payload={"moves": []},
        analysis_md="# Analysis",
        coach_md="Coach output",
        architect_md="Architect output",
        scenario_name="grid_ctf",
    )
    store.flush_writes()

    gen_dir = tmp_path / "runs" / "run_1" / "generations" / "gen_1"
    assert (gen_dir / "metrics.json").exists()
    assert (gen_dir / "replays" / "grid_ctf_1.json").exists()
    assert (tmp_path / "knowledge" / "grid_ctf" / "analysis" / "gen_1.md").exists()
    assert (tmp_path / "knowledge" / "grid_ctf" / "coach_history.md").exists()


def test_persist_generation_without_buffer(tmp_path: Path) -> None:
    """persist_generation works without buffering (default)."""
    store = ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
    )
    store.persist_generation(
        run_id="run_1",
        generation_index=1,
        metrics={"score": 0.5},
        replay_payload={"moves": []},
        analysis_md="# Analysis",
        coach_md="Coach output",
        architect_md="Architect output",
        scenario_name="grid_ctf",
    )
    gen_dir = tmp_path / "runs" / "run_1" / "generations" / "gen_1"
    assert (gen_dir / "metrics.json").exists()


def test_flush_and_shutdown(tmp_path: Path) -> None:
    """flush_writes and shutdown_writer are safe to call."""
    store = ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        enable_buffered_writes=True,
    )
    store.flush_writes()  # No-op before any writes
    store.shutdown_writer()


def test_playbook_write_stays_synchronous(tmp_path: Path) -> None:
    """write_playbook is NOT buffered -- it uses VersionedFileStore."""
    store = ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        enable_buffered_writes=True,
    )
    store.write_playbook("grid_ctf", "# Strategy\nBe aggressive.\n")
    # Should be immediately available (not buffered)
    content = store.read_playbook("grid_ctf")
    assert "Be aggressive" in content
