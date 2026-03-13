"""Tests for library persistence methods on ArtifactStore."""
from __future__ import annotations

import json
from pathlib import Path

from autocontext.storage.artifacts import ArtifactStore


def _make_store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / "claude_skills",
    )


# ---------------------------------------------------------------------------
# Librarian notes
# ---------------------------------------------------------------------------


def test_write_librarian_notes(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.write_librarian_notes(
        scenario="grid_ctf",
        book_name="clean-arch",
        generation=3,
        content="## Advisory\nUse SRP.\n",
    )
    path = tmp_path / "knowledge" / "grid_ctf" / "library" / "librarian_notes" / "clean-arch" / "gen_3.md"
    assert path.exists()
    assert "Use SRP" in path.read_text()


def test_read_cumulative_notes_empty(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    notes = store.read_cumulative_notes("grid_ctf", "clean-arch")
    assert notes == ""


def test_append_cumulative_notes(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.append_cumulative_notes("grid_ctf", "clean-arch", "Gen 1: SRP flagged.")
    store.append_cumulative_notes("grid_ctf", "clean-arch", "Gen 2: Team complied.")
    notes = store.read_cumulative_notes("grid_ctf", "clean-arch")
    assert "Gen 1" in notes
    assert "Gen 2" in notes


# ---------------------------------------------------------------------------
# Archivist decisions
# ---------------------------------------------------------------------------


def test_write_archivist_decision(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.write_archivist_decision(
        scenario="grid_ctf",
        generation=5,
        content="## Decision\nhard_gate on SRP violation.\n",
    )
    path = tmp_path / "knowledge" / "grid_ctf" / "library" / "archivist" / "decisions" / "gen_5_decision.md"
    assert path.exists()


# ---------------------------------------------------------------------------
# Active books and consultation log
# ---------------------------------------------------------------------------


def test_write_active_books(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.write_active_books("grid_ctf", ["clean-arch", "ddd"])
    path = tmp_path / "knowledge" / "grid_ctf" / "library" / "active_books.json"
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["books"] == ["clean-arch", "ddd"]


def test_write_consultation_log(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.append_consultation_log("grid_ctf", "## Gen 1\n### analyst -> clean-arch\n**Q:** SRP?\n")
    path = tmp_path / "knowledge" / "grid_ctf" / "library" / "archivist" / "consultation_log.md"
    assert path.exists()
    assert "SRP?" in path.read_text()
