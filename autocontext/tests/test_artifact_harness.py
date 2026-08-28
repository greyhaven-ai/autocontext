"""Tests for ArtifactStore harness directory CRUD operations (AC-73)."""
from __future__ import annotations

from pathlib import Path

import pytest

from autocontext.security.confined_files import ConfinedFileTooLarge, ConfinedPathError
from autocontext.storage import artifact_harness_codegen
from autocontext.storage.artifact_harness_codegen import MAX_HARNESS_SOURCE_BYTES
from autocontext.storage.artifacts import ArtifactStore


def _make_store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
    )


# ---------------------------------------------------------------------------
# harness_dir
# ---------------------------------------------------------------------------


class TestHarnessDir:
    def test_harness_dir_path(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        assert store.harness_dir("grid_ctf") == tmp_path / "knowledge" / "grid_ctf" / "harness"


# ---------------------------------------------------------------------------
# write_harness
# ---------------------------------------------------------------------------


class TestWriteHarness:
    def test_write_creates_file(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        result = store.write_harness("grid_ctf", "validate_strategy", "def validate(s): return True\n")
        assert result.exists()
        assert result.name == "validate_strategy.py"
        assert "def validate(s)" in result.read_text()

    def test_write_creates_directory_lazily(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        h_dir = store.harness_dir("grid_ctf")
        assert not h_dir.exists()
        store.write_harness("grid_ctf", "check", "pass")
        assert h_dir.exists()

    def test_write_overwrites_existing(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.write_harness("grid_ctf", "check", "# version 1")
        store.write_harness("grid_ctf", "check", "# version 2")
        content = store.read_harness("grid_ctf", "check")
        assert content == "# version 2"

    def test_write_returns_path(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        path = store.write_harness("othello", "legal_moves", "pass")
        assert isinstance(path, Path)
        assert path.parent.name == "harness"

    def test_write_rejects_invalid_name(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        with pytest.raises(ValueError, match="invalid harness name"):
            store.write_harness("grid_ctf", "../escape", "pass")

    def test_write_rejects_oversized_source(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)

        with pytest.raises(ConfinedFileTooLarge, match="byte limit"):
            store.write_harness("grid_ctf", "validator", "x" * (MAX_HARNESS_SOURCE_BYTES + 1))

        assert not (tmp_path / "knowledge" / "grid_ctf" / "harness" / "validator.py").exists()


# ---------------------------------------------------------------------------
# read_harness
# ---------------------------------------------------------------------------


class TestReadHarness:
    def test_read_existing(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.write_harness("grid_ctf", "validator", "# harness code\n")
        result = store.read_harness("grid_ctf", "validator")
        assert result == "# harness code\n"

    def test_read_missing_returns_none(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        assert store.read_harness("grid_ctf", "nonexistent") is None

    def test_read_missing_scenario_returns_none(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        assert store.read_harness("no_scenario", "anything") is None

    def test_read_rejects_invalid_name(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        with pytest.raises(ValueError, match="invalid harness name"):
            store.read_harness("grid_ctf", "../../etc/passwd")


# ---------------------------------------------------------------------------
# list_harness
# ---------------------------------------------------------------------------


class TestListHarness:
    def test_list_empty_scenario(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        assert store.list_harness("grid_ctf") == []

    def test_list_returns_sorted_names(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.write_harness("grid_ctf", "validate_strategy", "pass")
        store.write_harness("grid_ctf", "check_bounds", "pass")
        store.write_harness("grid_ctf", "parse_state", "pass")
        result = store.list_harness("grid_ctf")
        assert result == ["check_bounds", "parse_state", "validate_strategy"]

    def test_list_excludes_archive_files(self, tmp_path: Path) -> None:
        """Files starting with _ (like _archive/) should be excluded."""
        store = _make_store(tmp_path)
        store.write_harness("grid_ctf", "validator", "pass")
        # Create an _archive directory with a .py file inside
        archive_dir = store.harness_dir("grid_ctf") / "_archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        (archive_dir / "old_gen1.py").write_text("# archived", encoding="utf-8")
        result = store.list_harness("grid_ctf")
        assert result == ["validator"]

    def test_list_excludes_non_py_files(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.write_harness("grid_ctf", "validator", "pass")
        # Create a non-py file
        (store.harness_dir("grid_ctf") / "README.md").write_text("# info", encoding="utf-8")
        result = store.list_harness("grid_ctf")
        assert result == ["validator"]

    def test_list_multiple_scenarios_isolated(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.write_harness("grid_ctf", "a_check", "pass")
        store.write_harness("othello", "b_check", "pass")
        assert store.list_harness("grid_ctf") == ["a_check"]
        assert store.list_harness("othello") == ["b_check"]


# ---------------------------------------------------------------------------
# read_harness_context
# ---------------------------------------------------------------------------


class TestReadHarnessContext:
    def test_context_no_harness(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        result = store.read_harness_context("grid_ctf")
        assert result == "No harness validators available."

    def test_context_combines_files(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.write_harness("grid_ctf", "alpha", "# alpha code")
        store.write_harness("grid_ctf", "beta", "# beta code")
        result = store.read_harness_context("grid_ctf")
        assert "### alpha.py" in result
        assert "### beta.py" in result
        assert "# alpha code" in result
        assert "# beta code" in result

    def test_context_files_in_python_blocks(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.write_harness("grid_ctf", "validator", "def check(): pass")
        result = store.read_harness_context("grid_ctf")
        assert "```python" in result
        assert "```" in result

    def test_context_enforces_aggregate_size_limit(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        store = _make_store(tmp_path)
        store.write_harness("grid_ctf", "alpha", "a" * 32)
        store.write_harness("grid_ctf", "beta", "b" * 32)
        monkeypatch.setattr(artifact_harness_codegen, "MAX_HARNESS_CONTEXT_BYTES", 64)

        with pytest.raises(ConfinedFileTooLarge, match="context"):
            store.read_harness_context("grid_ctf")


# ---------------------------------------------------------------------------
# confined filesystem boundary
# ---------------------------------------------------------------------------


class TestHarnessFilesystemConfinement:
    @staticmethod
    def _assert_confined_operations_fail(store: ArtifactStore) -> None:
        with pytest.raises(ConfinedPathError):
            store.write_harness("grid_ctf", "validator", "new content")
        with pytest.raises(ConfinedPathError):
            store.read_harness("grid_ctf", "validator")
        with pytest.raises(ConfinedPathError):
            store.list_harness("grid_ctf")
        with pytest.raises(ConfinedPathError):
            store.read_harness_context("grid_ctf")

    def test_scenario_directory_symlink_cannot_escape_knowledge_root(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        outside = tmp_path / "outside"
        outside.mkdir()
        store.knowledge_root.mkdir()
        (store.knowledge_root / "grid_ctf").symlink_to(outside, target_is_directory=True)

        self._assert_confined_operations_fail(store)

        assert not (outside / "harness").exists()

    def test_harness_directory_symlink_cannot_escape_scenario(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        outside = tmp_path / "outside"
        outside.mkdir()
        scenario_dir = store.knowledge_root / "grid_ctf"
        scenario_dir.mkdir(parents=True)
        (scenario_dir / "harness").symlink_to(outside, target_is_directory=True)

        self._assert_confined_operations_fail(store)

        assert not (outside / "validator.py").exists()

    def test_final_file_symlink_is_not_read_listed_or_overwritten(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        outside = tmp_path / "outside.py"
        outside.write_text("secret", encoding="utf-8")
        harness_dir = store.knowledge_root / "grid_ctf" / "harness"
        harness_dir.mkdir(parents=True)
        (harness_dir / "validator.py").symlink_to(outside)

        with pytest.raises(ConfinedPathError):
            store.read_harness("grid_ctf", "validator")
        with pytest.raises(ConfinedPathError):
            store.write_harness("grid_ctf", "validator", "overwritten")
        assert store.list_harness("grid_ctf") == []
        assert store.read_harness_context("grid_ctf") == "No harness validators available."
        assert outside.read_text(encoding="utf-8") == "secret"

    def test_oversized_existing_file_is_not_read_into_context(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        harness_dir = store.knowledge_root / "grid_ctf" / "harness"
        harness_dir.mkdir(parents=True)
        (harness_dir / "validator.py").write_bytes(b"x" * (MAX_HARNESS_SOURCE_BYTES + 1))

        with pytest.raises(ConfinedFileTooLarge, match="byte limit"):
            store.read_harness("grid_ctf", "validator")
        with pytest.raises(ConfinedFileTooLarge, match="byte limit"):
            store.read_harness_context("grid_ctf")


# ---------------------------------------------------------------------------
# write_harness + read_harness roundtrip
# ---------------------------------------------------------------------------


class TestHarnessRoundtrip:
    def test_write_read_roundtrip(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        source = 'def validate(strategy, scenario):\n    return True, []\n'
        store.write_harness("grid_ctf", "validate_strategy", source)
        assert store.read_harness("grid_ctf", "validate_strategy") == source

    def test_list_reflects_writes(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        assert store.list_harness("grid_ctf") == []
        store.write_harness("grid_ctf", "first", "pass")
        assert store.list_harness("grid_ctf") == ["first"]
        store.write_harness("grid_ctf", "second", "pass")
        assert store.list_harness("grid_ctf") == ["first", "second"]
