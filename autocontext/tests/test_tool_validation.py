"""Tests for Gap 4: persist_tools() validates syntax via ast.parse."""
from __future__ import annotations

from pathlib import Path

from autocontext.storage.artifacts import ArtifactStore


def _make_store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
    )


def test_valid_tool_persisted(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    tools = [{"name": "good_tool", "code": "def run(x):\n    return x + 1", "description": "A valid tool"}]
    created = store.persist_tools("grid_ctf", 1, tools)
    assert "good_tool.py" in created
    assert (store.tools_dir("grid_ctf") / "good_tool.py").exists()


def test_invalid_syntax_skipped(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    tools = [{"name": "bad_tool", "code": "def run(:\n    broken syntax", "description": "Broken"}]
    created = store.persist_tools("grid_ctf", 1, tools)
    assert created == []
    assert not (store.tools_dir("grid_ctf") / "bad_tool.py").exists()


def test_empty_code_skipped(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    tools = [{"name": "empty_tool", "code": "", "description": "Empty code"}]
    created = store.persist_tools("grid_ctf", 1, tools)
    assert created == []


def test_partial_batch_persists_valid_only(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    tools = [
        {"name": "valid_one", "code": "x = 1", "description": "Valid"},
        {"name": "broken", "code": "def (:\n  pass", "description": "Invalid syntax"},
        {"name": "valid_two", "code": "y = 2", "description": "Also valid"},
    ]
    created = store.persist_tools("grid_ctf", 1, tools)
    assert "valid_one.py" in created
    assert "valid_two.py" in created
    assert "broken.py" not in created
    assert (store.tools_dir("grid_ctf") / "valid_one.py").exists()
    assert not (store.tools_dir("grid_ctf") / "broken.py").exists()
    assert (store.tools_dir("grid_ctf") / "valid_two.py").exists()
