"""Tests for Gap 7: Cross-scenario shared tools directory."""
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


def test_shared_tools_dir_created(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    shared_dir = store.shared_tools_dir()
    assert shared_dir == tmp_path / "knowledge" / "_shared" / "tools"
    # Persist a shared tool
    shared_dir.mkdir(parents=True, exist_ok=True)
    (shared_dir / "normalize.py").write_text("def normalize(x): return x / max(x)\n", encoding="utf-8")
    assert (shared_dir / "normalize.py").exists()


def test_read_tool_context_includes_shared(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    # Create scenario-specific tool
    scenario_dir = store.tools_dir("grid_ctf")
    scenario_dir.mkdir(parents=True, exist_ok=True)
    (scenario_dir / "specific.py").write_text("def specific(): pass\n", encoding="utf-8")

    # Create shared tool
    shared_dir = store.shared_tools_dir()
    shared_dir.mkdir(parents=True, exist_ok=True)
    (shared_dir / "common.py").write_text("def common(): pass\n", encoding="utf-8")

    context = store.read_tool_context("grid_ctf")
    assert "specific.py" in context
    assert "common.py" in context


def test_shared_tools_labeled_separately(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    # Create scenario-specific tool
    scenario_dir = store.tools_dir("grid_ctf")
    scenario_dir.mkdir(parents=True, exist_ok=True)
    (scenario_dir / "local.py").write_text("x = 1\n", encoding="utf-8")

    # Create shared tool
    shared_dir = store.shared_tools_dir()
    shared_dir.mkdir(parents=True, exist_ok=True)
    (shared_dir / "shared_util.py").write_text("y = 2\n", encoding="utf-8")

    context = store.read_tool_context("grid_ctf")
    assert "[shared]" in context
    assert "### local.py" in context
    assert "### [shared] shared_util.py" in context
