"""Tests for architect tool update behavior."""
from __future__ import annotations

from pathlib import Path

from autocontext.prompts.templates import build_prompt_bundle
from autocontext.scenarios.base import Observation
from autocontext.storage import ArtifactStore


def _make_store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(
        tmp_path / "runs",
        tmp_path / "knowledge",
        tmp_path / "skills",
        tmp_path / ".claude/skills",
    )


def test_new_tool_creates_file(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    tools = [{"name": "scorer", "code": "def run(x): return x", "description": "Score tool"}]
    created = store.persist_tools("grid_ctf", 1, tools)
    assert "scorer.py" in created
    assert (tmp_path / "knowledge" / "grid_ctf" / "tools" / "scorer.py").exists()


def test_update_overwrites_file(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    tools_v1 = [{"name": "scorer", "code": "def run(x): return x", "description": "V1"}]
    store.persist_tools("grid_ctf", 1, tools_v1)
    tools_v2 = [{"name": "scorer", "code": "def run(x): return x * 2", "description": "V2"}]
    created = store.persist_tools("grid_ctf", 2, tools_v2)
    assert any("updated" in c for c in created)
    content = (tmp_path / "knowledge" / "grid_ctf" / "tools" / "scorer.py").read_text()
    assert "x * 2" in content


def test_update_archives_old(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    tools_v1 = [{"name": "scorer", "code": "def run(x): return x", "description": "V1"}]
    store.persist_tools("grid_ctf", 1, tools_v1)
    tools_v2 = [{"name": "scorer", "code": "def run(x): return x * 2", "description": "V2"}]
    store.persist_tools("grid_ctf", 2, tools_v2)
    archive_dir = tmp_path / "knowledge" / "grid_ctf" / "tools" / "_archive"
    assert archive_dir.exists()
    archived = list(archive_dir.glob("scorer_gen*.py"))
    assert len(archived) == 1


def test_archive_filename_includes_gen(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    tools_v1 = [{"name": "scorer", "code": "def run(x): return x", "description": "V1"}]
    store.persist_tools("grid_ctf", 1, tools_v1)
    tools_v2 = [{"name": "scorer", "code": "def run(x): return x * 2", "description": "V2"}]
    store.persist_tools("grid_ctf", 2, tools_v2)
    archive_file = tmp_path / "knowledge" / "grid_ctf" / "tools" / "_archive" / "scorer_gen2.py"
    assert archive_file.exists()
    # Archive should contain the OLD content (V1)
    assert "return x" in archive_file.read_text()


def test_update_tagged_in_list(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    tools_v1 = [{"name": "scorer", "code": "def run(x): return x", "description": "V1"}]
    store.persist_tools("grid_ctf", 1, tools_v1)
    tools_v2 = [{"name": "scorer", "code": "def run(x): return x * 2", "description": "V2"}]
    created = store.persist_tools("grid_ctf", 2, tools_v2)
    assert "scorer.py (updated)" in created


def test_prompt_mentions_update(tmp_path: Path) -> None:
    prompts = build_prompt_bundle(
        scenario_rules="rules",
        strategy_interface="{}",
        evaluation_criteria="criteria",
        previous_summary="best: 0.0",
        observation=Observation(narrative="n", state={}, constraints=[]),
        current_playbook="playbook",
        available_tools="tools",
    )
    assert "UPDATE existing tools" in prompts.architect
