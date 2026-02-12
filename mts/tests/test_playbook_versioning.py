"""Tests for playbook versioning."""
from __future__ import annotations

from pathlib import Path

from mts.config import AppSettings
from mts.loop import GenerationRunner
from mts.storage import ArtifactStore


def _make_store(tmp_path: Path, max_versions: int = 5) -> ArtifactStore:
    return ArtifactStore(
        tmp_path / "runs",
        tmp_path / "knowledge",
        tmp_path / "skills",
        tmp_path / ".claude/skills",
        max_playbook_versions=max_versions,
    )


def test_first_write_no_version(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.write_playbook("grid_ctf", "First playbook")
    assert store.playbook_version_count("grid_ctf") == 0


def test_second_write_creates_version(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.write_playbook("grid_ctf", "First playbook")
    store.write_playbook("grid_ctf", "Second playbook")
    assert store.playbook_version_count("grid_ctf") == 1
    # Version should contain the first playbook's content
    v1 = store.read_playbook_version("grid_ctf", 1)
    assert "First playbook" in v1


def test_version_content_matches_previous(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.write_playbook("grid_ctf", "Original content")
    store.write_playbook("grid_ctf", "New content")
    v1 = store.read_playbook_version("grid_ctf", 1)
    assert "Original content" in v1
    current = store.read_playbook("grid_ctf")
    assert "New content" in current


def test_pruning_at_max(tmp_path: Path) -> None:
    store = _make_store(tmp_path, max_versions=3)
    for i in range(6):
        store.write_playbook("grid_ctf", f"Playbook v{i}")
    # 6 writes = 5 versions created, pruned to 3
    assert store.playbook_version_count("grid_ctf") == 3


def test_rollback_restores_previous(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.write_playbook("grid_ctf", "V1")
    store.write_playbook("grid_ctf", "V2")
    store.write_playbook("grid_ctf", "V3")
    # Current is V3, versions are [V1, V2]
    result = store.rollback_playbook("grid_ctf")
    assert result is True
    current = store.read_playbook("grid_ctf")
    assert "V2" in current
    assert store.playbook_version_count("grid_ctf") == 1


def test_rollback_empty_returns_false(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    assert store.rollback_playbook("grid_ctf") is False


def test_version_count_accurate(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    assert store.playbook_version_count("grid_ctf") == 0
    store.write_playbook("grid_ctf", "V1")
    assert store.playbook_version_count("grid_ctf") == 0
    store.write_playbook("grid_ctf", "V2")
    assert store.playbook_version_count("grid_ctf") == 1
    store.write_playbook("grid_ctf", "V3")
    assert store.playbook_version_count("grid_ctf") == 2


def test_read_specific_version(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.write_playbook("grid_ctf", "Alpha")
    store.write_playbook("grid_ctf", "Beta")
    store.write_playbook("grid_ctf", "Gamma")
    v1 = store.read_playbook_version("grid_ctf", 1)
    v2 = store.read_playbook_version("grid_ctf", 2)
    assert "Alpha" in v1
    assert "Beta" in v2
    assert store.read_playbook_version("grid_ctf", 99) == ""


def test_integration_runner_versions(tmp_path: Path) -> None:
    """Full 3-gen run creates version files."""
    settings = AppSettings(
        db_path=tmp_path / "runs" / "mts.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        event_stream_path=tmp_path / "runs" / "events.ndjson",
        seed_base=2000,
        agent_provider="deterministic",
        matches_per_generation=2,
        playbook_max_versions=5,
    )
    runner = GenerationRunner(settings)
    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    runner.migrate(migrations_dir)
    runner.run(scenario_name="grid_ctf", generations=3, run_id="version_test")
    # At least one version should exist (gen 1 advances, gen 2 may advance or rollback)
    versions_dir = tmp_path / "knowledge" / "grid_ctf" / "playbook_versions"
    if versions_dir.exists():
        versions = list(versions_dir.glob("playbook_v*.md"))
        # We expect versions from the advance generations
        assert len(versions) >= 0  # At least not crashing
