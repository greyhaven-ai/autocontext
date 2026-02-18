"""Tests for mts.harness.storage.versioned_store — versioned file store."""

from __future__ import annotations

from pathlib import Path

import pytest

from mts.harness.storage.versioned_store import VersionedFileStore


@pytest.fixture()
def store(tmp_path: Path) -> VersionedFileStore:
    return VersionedFileStore(root=tmp_path, max_versions=3)


class TestVersionedFileStore:
    def test_write_creates_file(self, store: VersionedFileStore, tmp_path: Path) -> None:
        store.write("doc.md", "first content")
        assert (tmp_path / "doc.md").exists()
        assert (tmp_path / "doc.md").read_text() == "first content"

    def test_write_archives_previous(self, store: VersionedFileStore) -> None:
        store.write("doc.md", "v1")
        store.write("doc.md", "v2")
        assert store.version_count("doc.md") == 1
        assert store.read_version("doc.md", 1) == "v1"

    def test_read_returns_current(self, store: VersionedFileStore) -> None:
        store.write("doc.md", "current")
        assert store.read("doc.md") == "current"

    def test_read_missing_returns_default(self, store: VersionedFileStore) -> None:
        assert store.read("missing.md") == ""
        assert store.read("missing.md", default="fallback") == "fallback"

    def test_rollback_restores_latest_archive(self, store: VersionedFileStore) -> None:
        store.write("doc.md", "v1")
        store.write("doc.md", "v2")
        store.write("doc.md", "v3")
        # Versions: v1, v2 archived; current is v3
        assert store.rollback("doc.md") is True
        assert store.read("doc.md") == "v2"

    def test_rollback_empty_returns_false(self, store: VersionedFileStore) -> None:
        assert store.rollback("nonexistent.md") is False

    def test_version_count_increments(self, store: VersionedFileStore) -> None:
        assert store.version_count("doc.md") == 0
        store.write("doc.md", "v1")
        assert store.version_count("doc.md") == 0  # no archive yet
        store.write("doc.md", "v2")
        assert store.version_count("doc.md") == 1
        store.write("doc.md", "v3")
        assert store.version_count("doc.md") == 2

    def test_read_version_by_number(self, store: VersionedFileStore) -> None:
        store.write("doc.md", "first")
        store.write("doc.md", "second")
        store.write("doc.md", "third")
        assert store.read_version("doc.md", 1) == "first"
        assert store.read_version("doc.md", 2) == "second"

    def test_prune_keeps_max_versions(self, store: VersionedFileStore) -> None:
        # max_versions=3, write 5 times → 4 archives, pruned to 3
        store.write("doc.md", "v1")
        store.write("doc.md", "v2")
        store.write("doc.md", "v3")
        store.write("doc.md", "v4")
        store.write("doc.md", "v5")
        assert store.version_count("doc.md") == 3
        # Oldest (v1) should have been pruned
        assert store.read_version("doc.md", 1) == ""
        # v2, v3, v4 survive
        assert store.read_version("doc.md", 2) == "v2"

    def test_version_numbers_monotonic(self, store: VersionedFileStore) -> None:
        for i in range(1, 6):
            store.write("doc.md", f"v{i}")
        # Even after pruning, version numbers only increase
        # v1 pruned, v2/v3/v4 remain as versions 2,3,4
        assert store.read_version("doc.md", 2) == "v2"
        assert store.read_version("doc.md", 3) == "v3"
        assert store.read_version("doc.md", 4) == "v4"

    def test_multiple_files_independent(self, store: VersionedFileStore) -> None:
        store.write("a.md", "a-v1")
        store.write("b.md", "b-v1")
        store.write("a.md", "a-v2")
        assert store.read("a.md") == "a-v2"
        assert store.read("b.md") == "b-v1"
        assert store.version_count("a.md") == 1
        assert store.version_count("b.md") == 0


class TestVersionedFileStoreCustomNaming:
    def test_custom_prefix_and_suffix(self, tmp_path: Path) -> None:
        store = VersionedFileStore(
            root=tmp_path,
            max_versions=3,
            versions_dir_name="playbook_versions",
            version_prefix="playbook_v",
            version_suffix=".md",
        )
        store.write("playbook.md", "v1")
        store.write("playbook.md", "v2")
        versions_dir = tmp_path / "playbook_versions"
        assert versions_dir.exists()
        assert (versions_dir / "playbook_v0001.md").exists()
        assert (versions_dir / "playbook_v0001.md").read_text() == "v1"

    def test_custom_naming_rollback(self, tmp_path: Path) -> None:
        store = VersionedFileStore(
            root=tmp_path,
            max_versions=3,
            versions_dir_name="playbook_versions",
            version_prefix="playbook_v",
            version_suffix=".md",
        )
        store.write("playbook.md", "v1")
        store.write("playbook.md", "v2")
        assert store.rollback("playbook.md") is True
        assert store.read("playbook.md") == "v1"

    def test_custom_naming_prune(self, tmp_path: Path) -> None:
        store = VersionedFileStore(
            root=tmp_path,
            max_versions=2,
            versions_dir_name="playbook_versions",
            version_prefix="playbook_v",
            version_suffix=".md",
        )
        for i in range(1, 5):
            store.write("playbook.md", f"v{i}")
        assert store.version_count("playbook.md") == 2
