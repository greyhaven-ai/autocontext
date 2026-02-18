"""Tests verifying ArtifactStore playbook methods delegate to VersionedFileStore."""
from __future__ import annotations

from pathlib import Path

import pytest

from mts.storage.artifacts import ArtifactStore


@pytest.fixture()
def store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        max_playbook_versions=3,
    )


class TestPlaybookDelegation:
    def test_write_playbook_creates_file(self, store: ArtifactStore) -> None:
        store.write_playbook("grid_ctf", "content v1")
        assert store.read_playbook("grid_ctf") != "No playbook yet. Start from scenario rules and observation."
        assert "content v1" in store.read_playbook("grid_ctf")

    def test_write_archives_previous(self, store: ArtifactStore) -> None:
        store.write_playbook("grid_ctf", "v1")
        store.write_playbook("grid_ctf", "v2")
        assert store.playbook_version_count("grid_ctf") == 1

    def test_rollback_restores(self, store: ArtifactStore) -> None:
        store.write_playbook("grid_ctf", "v1")
        store.write_playbook("grid_ctf", "v2")
        assert store.rollback_playbook("grid_ctf") is True
        assert "v1" in store.read_playbook("grid_ctf")

    def test_version_file_layout_matches_legacy(self, store: ArtifactStore, tmp_path: Path) -> None:
        store.write_playbook("grid_ctf", "v1")
        store.write_playbook("grid_ctf", "v2")
        versions_dir = tmp_path / "knowledge" / "grid_ctf" / "playbook_versions"
        assert versions_dir.exists()
        files = list(versions_dir.glob("playbook_v*.md"))
        assert len(files) == 1
        assert files[0].name == "playbook_v0001.md"

    def test_has_playbook_store_attribute(self, store: ArtifactStore) -> None:
        """ArtifactStore should expose _playbook_stores dict."""
        assert hasattr(store, "_playbook_stores")
