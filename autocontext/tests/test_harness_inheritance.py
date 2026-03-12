"""Tests for harness inheritance in ArtifactStore lifecycle (MTS-92)."""
from __future__ import annotations

from pathlib import Path

import pytest

from autocontext.storage.artifacts import ArtifactStore


@pytest.fixture()
def store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
    )


class TestListHarness:
    """list_harness returns names of .py files in the harness directory."""

    def test_empty_when_no_dir(self, store: ArtifactStore) -> None:
        assert store.list_harness("grid_ctf") == []

    def test_empty_when_dir_exists_but_no_files(self, store: ArtifactStore) -> None:
        store.harness_dir("grid_ctf").mkdir(parents=True)
        assert store.list_harness("grid_ctf") == []

    def test_lists_py_files_without_extension(self, store: ArtifactStore) -> None:
        h_dir = store.harness_dir("grid_ctf")
        h_dir.mkdir(parents=True)
        (h_dir / "validate_move.py").write_text("def v(): ...", encoding="utf-8")
        (h_dir / "score_action.py").write_text("def s(): ...", encoding="utf-8")
        result = store.list_harness("grid_ctf")
        assert result == ["score_action", "validate_move"]

    def test_excludes_non_py_files(self, store: ArtifactStore) -> None:
        h_dir = store.harness_dir("grid_ctf")
        h_dir.mkdir(parents=True)
        (h_dir / "validate_move.py").write_text("code", encoding="utf-8")
        (h_dir / "readme.md").write_text("docs", encoding="utf-8")
        (h_dir / "harness_version.json").write_text("{}", encoding="utf-8")
        assert store.list_harness("grid_ctf") == ["validate_move"]

    def test_excludes_archive_subdirectory(self, store: ArtifactStore) -> None:
        h_dir = store.harness_dir("grid_ctf")
        h_dir.mkdir(parents=True)
        archive = h_dir / "_archive"
        archive.mkdir()
        (archive / "old_v1.py").write_text("old", encoding="utf-8")
        (h_dir / "validate_move.py").write_text("code", encoding="utf-8")
        # _archive/*.py should not show up since we glob in h_dir only
        assert store.list_harness("grid_ctf") == ["validate_move"]


class TestSnapshotIncludesHarness:
    """snapshot_knowledge includes harness files in the snapshot."""

    def test_snapshot_copies_harness_files(self, store: ArtifactStore) -> None:
        # Create playbook (required for snapshot to work)
        pb_dir = store.knowledge_root / "grid_ctf"
        pb_dir.mkdir(parents=True)
        (pb_dir / "playbook.md").write_text("# Playbook", encoding="utf-8")

        # Create harness files
        h_dir = store.harness_dir("grid_ctf")
        h_dir.mkdir(parents=True)
        (h_dir / "validate_move.py").write_text("def v(): ...", encoding="utf-8")
        (h_dir / "score_action.py").write_text("def s(): ...", encoding="utf-8")

        store.snapshot_knowledge("grid_ctf", "run_001")

        snapshot_harness = store.knowledge_root / "grid_ctf" / "snapshots" / "run_001" / "harness"
        assert snapshot_harness.exists()
        assert (snapshot_harness / "validate_move.py").read_text(encoding="utf-8") == "def v(): ..."
        assert (snapshot_harness / "score_action.py").read_text(encoding="utf-8") == "def s(): ..."

    def test_snapshot_no_harness_dir_is_fine(self, store: ArtifactStore) -> None:
        pb_dir = store.knowledge_root / "grid_ctf"
        pb_dir.mkdir(parents=True)
        (pb_dir / "playbook.md").write_text("# PB", encoding="utf-8")

        store.snapshot_knowledge("grid_ctf", "run_002")

        snapshot_harness = store.knowledge_root / "grid_ctf" / "snapshots" / "run_002" / "harness"
        assert not snapshot_harness.exists()


class TestRestoreIncludesHarness:
    """restore_knowledge_snapshot restores harness files from snapshot."""

    def test_restore_copies_harness_files(self, store: ArtifactStore) -> None:
        # Create snapshot with harness
        snapshot_dir = store.knowledge_root / "grid_ctf" / "snapshots" / "run_001"
        harness_snap = snapshot_dir / "harness"
        harness_snap.mkdir(parents=True)
        (harness_snap / "validate_move.py").write_text("def v(): ...", encoding="utf-8")

        result = store.restore_knowledge_snapshot("grid_ctf", "run_001")
        assert result is True

        h_dir = store.harness_dir("grid_ctf")
        assert (h_dir / "validate_move.py").read_text(encoding="utf-8") == "def v(): ..."

    def test_restore_no_harness_snapshot_is_fine(self, store: ArtifactStore) -> None:
        # Snapshot with only playbook, no harness
        snapshot_dir = store.knowledge_root / "grid_ctf" / "snapshots" / "run_002"
        snapshot_dir.mkdir(parents=True)
        (snapshot_dir / "playbook.md").write_text("# PB", encoding="utf-8")

        result = store.restore_knowledge_snapshot("grid_ctf", "run_002")
        assert result is True
        assert not store.harness_dir("grid_ctf").exists()

    def test_roundtrip_snapshot_restore(self, store: ArtifactStore) -> None:
        """Snapshot then restore preserves harness files."""
        # Setup: playbook + harness
        pb_dir = store.knowledge_root / "grid_ctf"
        pb_dir.mkdir(parents=True)
        (pb_dir / "playbook.md").write_text("# PB", encoding="utf-8")
        h_dir = store.harness_dir("grid_ctf")
        h_dir.mkdir(parents=True)
        (h_dir / "validate_move.py").write_text("original code", encoding="utf-8")

        # Snapshot
        store.snapshot_knowledge("grid_ctf", "run_x")

        # Delete current harness
        (h_dir / "validate_move.py").unlink()
        h_dir.rmdir()

        # Restore
        store.restore_knowledge_snapshot("grid_ctf", "run_x")
        assert (h_dir / "validate_move.py").read_text(encoding="utf-8") == "original code"
