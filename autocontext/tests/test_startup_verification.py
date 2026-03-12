"""Tests for session startup verification (MTS-22)."""
from __future__ import annotations

import json
from pathlib import Path

from autocontext.loop.startup_verification import verify_startup


def test_all_checks_pass(tmp_path: Path) -> None:
    """Clean state passes all checks."""
    knowledge = tmp_path / "grid_ctf"
    knowledge.mkdir()
    (knowledge / "playbook.md").write_text("# Playbook\nContent here.\n")
    (knowledge / "progress.json").write_text(json.dumps({"generation": 1}))

    report = verify_startup(
        scenario_name="grid_ctf",
        knowledge_root=tmp_path,
        db_path=None,  # Skip DB check
    )

    assert len(report.warnings) == 0


def test_missing_knowledge_dir(tmp_path: Path) -> None:
    """Missing knowledge dir is a warning, not a failure (first run)."""
    report = verify_startup(
        scenario_name="grid_ctf",
        knowledge_root=tmp_path,
        db_path=None,
    )
    assert any("knowledge directory" in w.lower() for w in report.warnings)


def test_invalid_progress_json(tmp_path: Path) -> None:
    """Malformed progress.json produces a warning."""
    knowledge = tmp_path / "grid_ctf"
    knowledge.mkdir()
    (knowledge / "playbook.md").write_text("# Playbook\n")
    (knowledge / "progress.json").write_text("NOT JSON")

    report = verify_startup(
        scenario_name="grid_ctf",
        knowledge_root=tmp_path,
        db_path=None,
    )
    assert any("progress.json" in w for w in report.warnings)


def test_empty_playbook_warning(tmp_path: Path) -> None:
    """Empty playbook is a warning."""
    knowledge = tmp_path / "grid_ctf"
    knowledge.mkdir()
    (knowledge / "playbook.md").write_text("")

    report = verify_startup(
        scenario_name="grid_ctf",
        knowledge_root=tmp_path,
        db_path=None,
    )

    assert any("playbook" in w.lower() for w in report.warnings)


def test_db_check_skipped_when_none(tmp_path: Path) -> None:
    """No DB path skips DB check cleanly."""
    verify_startup(
        scenario_name="grid_ctf",
        knowledge_root=tmp_path,
        db_path=None,
    )

