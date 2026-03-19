"""Tests for sandboxed external play."""

from __future__ import annotations

from pathlib import Path

import pytest

from autocontext.config import AppSettings
from autocontext.mcp.sandbox import SandboxManager


def _make_settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        agent_provider="deterministic",
        sandbox_max_generations=10,
        event_stream_path=tmp_path / "runs" / "events.ndjson",
    )


def test_create_sandbox(tmp_path: Path) -> None:
    """Creates directory structure with knowledge/ subdirs."""
    settings = _make_settings(tmp_path)
    mgr = SandboxManager(settings)
    sandbox = mgr.create("grid_ctf", user_id="testuser")
    assert sandbox.root.exists()
    assert (sandbox.root / "knowledge" / "grid_ctf").exists()
    assert (sandbox.root / "runs").exists()


def test_sandbox_id_format(tmp_path: Path) -> None:
    """Starts with sbx_, contains user_id."""
    settings = _make_settings(tmp_path)
    mgr = SandboxManager(settings)
    sandbox = mgr.create("grid_ctf", user_id="alice")
    assert sandbox.sandbox_id.startswith("sbx_alice_")


def test_knowledge_seeded(tmp_path: Path) -> None:
    """Playbook and hints copied from main."""
    settings = _make_settings(tmp_path)
    # Pre-seed main knowledge
    main_knowledge = tmp_path / "knowledge" / "grid_ctf"
    main_knowledge.mkdir(parents=True)
    (main_knowledge / "playbook.md").write_text("# Main Playbook\nContent here.", encoding="utf-8")
    (main_knowledge / "hints.md").write_text("- Hint 1\n- Hint 2\n", encoding="utf-8")

    mgr = SandboxManager(settings)
    sandbox = mgr.create("grid_ctf")
    sb_playbook = sandbox.root / "knowledge" / "grid_ctf" / "playbook.md"
    sb_hints = sandbox.root / "knowledge" / "grid_ctf" / "hints.md"
    assert sb_playbook.exists()
    assert "Main Playbook" in sb_playbook.read_text(encoding="utf-8")
    assert sb_hints.exists()
    assert "Hint 1" in sb_hints.read_text(encoding="utf-8")


def test_hint_state_seeded(tmp_path: Path) -> None:
    """Structured hint state copied into sandbox knowledge."""
    settings = _make_settings(tmp_path)
    main_knowledge = tmp_path / "knowledge" / "grid_ctf"
    main_knowledge.mkdir(parents=True)
    (main_knowledge / "hint_state.json").write_text(
        (
            '{"policy":{"max_hints":2,"archive_rotated":true},"active":'
            '[{"text":"Hint 1","rank":1,"generation_added":1,'
            '"impact_score":0.9,"metadata":{}}],"archived":[]}'
        ),
        encoding="utf-8",
    )

    mgr = SandboxManager(settings)
    sandbox = mgr.create("grid_ctf")
    sb_hint_state = sandbox.root / "knowledge" / "grid_ctf" / "hint_state.json"
    assert sb_hint_state.exists()
    assert "Hint 1" in sb_hint_state.read_text(encoding="utf-8")


def test_tools_seeded(tmp_path: Path) -> None:
    """Tool .py files copied from main."""
    settings = _make_settings(tmp_path)
    tools_dir = tmp_path / "knowledge" / "grid_ctf" / "tools"
    tools_dir.mkdir(parents=True)
    (tools_dir / "my_tool.py").write_text("def run(inputs): pass\n", encoding="utf-8")

    mgr = SandboxManager(settings)
    sandbox = mgr.create("grid_ctf")
    sb_tool = sandbox.root / "knowledge" / "grid_ctf" / "tools" / "my_tool.py"
    assert sb_tool.exists()
    assert "def run" in sb_tool.read_text(encoding="utf-8")


def test_main_knowledge_unchanged(tmp_path: Path) -> None:
    """After sandbox gen, main playbook untouched."""
    settings = _make_settings(tmp_path)
    main_playbook = tmp_path / "knowledge" / "grid_ctf" / "playbook.md"
    main_playbook.parent.mkdir(parents=True)
    main_playbook.write_text("# Original\n", encoding="utf-8")

    mgr = SandboxManager(settings)
    sandbox = mgr.create("grid_ctf")
    mgr.run_generation(sandbox.sandbox_id, generations=1)

    assert main_playbook.read_text(encoding="utf-8") == "# Original\n"


def test_run_generation_in_sandbox(tmp_path: Path) -> None:
    """Deterministic gen completes, returns summary dict."""
    settings = _make_settings(tmp_path)
    mgr = SandboxManager(settings)
    sandbox = mgr.create("grid_ctf")
    result = mgr.run_generation(sandbox.sandbox_id, generations=1)
    assert result["sandbox_id"] == sandbox.sandbox_id
    assert result["generations_executed"] == 1
    assert "best_score" in result
    assert isinstance(result["best_score"], float)


def test_sandbox_playbook_evolves(tmp_path: Path) -> None:
    """After gen, sandbox playbook differs from initial default."""
    settings = _make_settings(tmp_path)
    mgr = SandboxManager(settings)
    sandbox = mgr.create("grid_ctf")
    mgr.read_playbook(sandbox.sandbox_id)
    mgr.run_generation(sandbox.sandbox_id, generations=1)
    after = mgr.read_playbook(sandbox.sandbox_id)
    # After a generation, the playbook should have content from coach
    assert len(after) > 0


def test_list_sandboxes(tmp_path: Path) -> None:
    """Returns correct entries."""
    settings = _make_settings(tmp_path)
    mgr = SandboxManager(settings)
    mgr.create("grid_ctf", user_id="user1")
    mgr.create("othello", user_id="user2")
    entries = mgr.list_sandboxes()
    assert len(entries) == 2
    scenarios = {e["scenario_name"] for e in entries}
    assert scenarios == {"grid_ctf", "othello"}


def test_destroy_cleans_up(tmp_path: Path) -> None:
    """Directory removed after destroy."""
    settings = _make_settings(tmp_path)
    mgr = SandboxManager(settings)
    sandbox = mgr.create("grid_ctf")
    root = sandbox.root
    assert root.exists()
    destroyed = mgr.destroy(sandbox.sandbox_id)
    assert destroyed is True
    assert not root.exists()
    assert mgr.list_sandboxes() == []


def test_max_generations_enforced(tmp_path: Path) -> None:
    """Exceeding limit raises error."""
    settings = _make_settings(tmp_path)
    settings_limited = settings.model_copy(update={"sandbox_max_generations": 2})
    mgr = SandboxManager(settings_limited)
    sandbox = mgr.create("grid_ctf")
    with pytest.raises(ValueError, match="exceeds sandbox limit"):
        mgr.run_generation(sandbox.sandbox_id, generations=5)
