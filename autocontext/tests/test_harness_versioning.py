"""Tests for ArtifactStore harness versioning (MTS-91)."""
from __future__ import annotations

import json
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


class TestWriteHarnessVersioned:
    """write_harness_versioned creates files, archives, and tracks versions."""

    def test_first_write_creates_file(self, store: ArtifactStore) -> None:
        path = store.write_harness_versioned("grid_ctf", "validate_move", "def v(): ...", generation=1)
        assert path.exists()
        assert path.read_text(encoding="utf-8") == "def v(): ..."

    def test_first_write_sets_version_metadata(self, store: ArtifactStore) -> None:
        store.write_harness_versioned("grid_ctf", "validate_move", "v1", generation=1)
        info = store.get_harness_version("grid_ctf")
        assert "validate_move" in info
        entry = info["validate_move"]
        assert isinstance(entry, dict)
        assert entry["generation"] == 1

    def test_second_write_archives_first(self, store: ArtifactStore) -> None:
        store.write_harness_versioned("grid_ctf", "validate_move", "v1", generation=1)
        store.write_harness_versioned("grid_ctf", "validate_move", "v2", generation=2)

        harness_dir = store.harness_dir("grid_ctf")
        archive_dir = harness_dir / "_archive"
        assert archive_dir.exists()
        archived = list(archive_dir.glob("v*.py"))
        assert len(archived) >= 1

    def test_second_write_updates_current_file(self, store: ArtifactStore) -> None:
        store.write_harness_versioned("grid_ctf", "validate_move", "v1", generation=1)
        path = store.write_harness_versioned("grid_ctf", "validate_move", "v2", generation=2)
        assert path.read_text(encoding="utf-8") == "v2"

    def test_version_metadata_increments(self, store: ArtifactStore) -> None:
        store.write_harness_versioned("grid_ctf", "validate_move", "v1", generation=1)
        store.write_harness_versioned("grid_ctf", "validate_move", "v2", generation=2)
        info = store.get_harness_version("grid_ctf")
        entry = info["validate_move"]
        assert isinstance(entry, dict)
        assert entry["version"] >= 2
        assert entry["generation"] == 2

    def test_multiple_harnesses_tracked_independently(self, store: ArtifactStore) -> None:
        store.write_harness_versioned("grid_ctf", "validate_move", "m1", generation=1)
        store.write_harness_versioned("grid_ctf", "score_action", "s1", generation=1)
        info = store.get_harness_version("grid_ctf")
        assert "validate_move" in info
        assert "score_action" in info

    def test_different_scenarios_isolated(self, store: ArtifactStore) -> None:
        store.write_harness_versioned("grid_ctf", "validate_move", "gc", generation=1)
        store.write_harness_versioned("othello", "validate_move", "ot", generation=1)

        gc_path = store.harness_dir("grid_ctf") / "validate_move.py"
        ot_path = store.harness_dir("othello") / "validate_move.py"
        assert gc_path.read_text(encoding="utf-8") == "gc"
        assert ot_path.read_text(encoding="utf-8") == "ot"

    def test_returns_correct_path(self, store: ArtifactStore) -> None:
        path = store.write_harness_versioned("grid_ctf", "validate_move", "code", generation=1)
        expected = store.harness_dir("grid_ctf") / "validate_move.py"
        assert path == expected

    @pytest.mark.parametrize("name", ["", "../escape", "bad/name", "contains space", "123abc"])
    def test_rejects_invalid_harness_name(self, store: ArtifactStore, name: str) -> None:
        with pytest.raises(ValueError, match="invalid harness name"):
            store.write_harness_versioned("grid_ctf", name, "code", generation=1)


class TestRollbackHarness:
    """rollback_harness restores previous versions from archive."""

    def test_rollback_no_archive_returns_none(self, store: ArtifactStore) -> None:
        store.write_harness_versioned("grid_ctf", "validate_move", "v1", generation=1)
        result = store.rollback_harness("grid_ctf", "validate_move")
        assert result is None

    def test_rollback_restores_previous_version(self, store: ArtifactStore) -> None:
        store.write_harness_versioned("grid_ctf", "validate_move", "v1", generation=1)
        store.write_harness_versioned("grid_ctf", "validate_move", "v2", generation=2)
        result = store.rollback_harness("grid_ctf", "validate_move")
        assert result == "v1"

    def test_rollback_updates_current_file(self, store: ArtifactStore) -> None:
        store.write_harness_versioned("grid_ctf", "validate_move", "v1", generation=1)
        store.write_harness_versioned("grid_ctf", "validate_move", "v2", generation=2)
        store.rollback_harness("grid_ctf", "validate_move")
        current = (store.harness_dir("grid_ctf") / "validate_move.py").read_text(encoding="utf-8")
        assert current == "v1"

    def test_rollback_decrements_version_metadata(self, store: ArtifactStore) -> None:
        store.write_harness_versioned("grid_ctf", "validate_move", "v1", generation=1)
        store.write_harness_versioned("grid_ctf", "validate_move", "v2", generation=2)
        store.rollback_harness("grid_ctf", "validate_move")
        info = store.get_harness_version("grid_ctf")
        entry = info["validate_move"]
        assert isinstance(entry, dict)
        # Version should have decremented
        assert entry["version"] < 3

    def test_rollback_nonexistent_harness_returns_none(self, store: ArtifactStore) -> None:
        result = store.rollback_harness("grid_ctf", "nonexistent")
        assert result is None

    def test_double_rollback_after_three_writes(self, store: ArtifactStore) -> None:
        store.write_harness_versioned("grid_ctf", "validate_move", "v1", generation=1)
        store.write_harness_versioned("grid_ctf", "validate_move", "v2", generation=2)
        store.write_harness_versioned("grid_ctf", "validate_move", "v3", generation=3)

        r1 = store.rollback_harness("grid_ctf", "validate_move")
        assert r1 == "v2"
        r2 = store.rollback_harness("grid_ctf", "validate_move")
        assert r2 == "v1"

    @pytest.mark.parametrize("name", ["", "../escape", "bad/name", "contains space", "123abc"])
    def test_rejects_invalid_harness_name(self, store: ArtifactStore, name: str) -> None:
        with pytest.raises(ValueError, match="invalid harness name"):
            store.rollback_harness("grid_ctf", name)


class TestGetHarnessVersion:
    """get_harness_version reads the version metadata JSON."""

    def test_empty_when_no_writes(self, store: ArtifactStore) -> None:
        info = store.get_harness_version("grid_ctf")
        assert info == {}

    def test_version_json_is_valid_json(self, store: ArtifactStore) -> None:
        store.write_harness_versioned("grid_ctf", "validate_move", "code", generation=1)
        path = store.harness_dir("grid_ctf") / "harness_version.json"
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)

    def test_harness_store_cached(self, store: ArtifactStore) -> None:
        """The VersionedFileStore is cached per scenario."""
        s1 = store._harness_store("grid_ctf")
        s2 = store._harness_store("grid_ctf")
        assert s1 is s2

    def test_different_scenarios_different_stores(self, store: ArtifactStore) -> None:
        s1 = store._harness_store("grid_ctf")
        s2 = store._harness_store("othello")
        assert s1 is not s2
