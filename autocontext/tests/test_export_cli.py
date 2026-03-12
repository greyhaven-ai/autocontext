"""Tests for autoctx export-training-data CLI command (AC-172)."""
from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from autocontext.cli import app
from autocontext.storage.artifacts import ArtifactStore
from autocontext.storage.sqlite_store import SQLiteStore

runner = CliRunner()


def _setup_db(tmp_path: Path) -> tuple[SQLiteStore, ArtifactStore, Path]:
    """Create and populate a test database with one run."""
    db_path = tmp_path / "test.sqlite3"
    db = SQLiteStore(db_path)
    db.migrate(Path("migrations"))
    artifacts = ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
    )

    db.create_run("run-1", "grid_ctf", 2, "local")
    db.upsert_generation("run-1", 1, 0.5, 0.5, 1000.0, 1, 0, "advance", "completed")
    db.upsert_generation("run-1", 2, 0.3, 0.3, 1000.0, 0, 1, "retry", "completed")
    db.append_agent_output("run-1", 1, "competitor", '{"aggression": 0.5}')
    db.append_agent_output("run-1", 2, "competitor", '{"aggression": 0.3}')
    db.insert_match("run-1", 1, seed=42, score=0.5, passed_validation=True, validation_errors="")

    return db, artifacts, db_path


def test_cli_export_with_run_id(tmp_path: Path) -> None:
    _, _, db_path = _setup_db(tmp_path)
    output_file = tmp_path / "out.jsonl"

    result = runner.invoke(
        app,
        [
            "export-training-data",
            "--run-id", "run-1",
            "--output", str(output_file),
            "--db-path", str(db_path),
        ],
    )
    assert result.exit_code == 0, result.output

    lines = output_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    rec = json.loads(lines[0])
    assert rec["run_id"] == "run-1"
    assert rec["generation_index"] == 1


def test_cli_export_with_scenario_all_runs(tmp_path: Path) -> None:
    db, _, db_path = _setup_db(tmp_path)
    # Add a second run for the same scenario
    db.create_run("run-2", "grid_ctf", 1, "local")
    db.upsert_generation("run-2", 1, 0.7, 0.7, 1000.0, 1, 0, "advance", "completed")
    db.append_agent_output("run-2", 1, "competitor", '{"aggression": 0.9}')

    output_file = tmp_path / "out.jsonl"
    result = runner.invoke(
        app,
        [
            "export-training-data",
            "--scenario", "grid_ctf",
            "--all-runs",
            "--output", str(output_file),
            "--db-path", str(db_path),
        ],
    )
    assert result.exit_code == 0, result.output

    lines = output_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3  # 2 from run-1 + 1 from run-2


def test_cli_output_is_valid_jsonl(tmp_path: Path) -> None:
    _, _, db_path = _setup_db(tmp_path)
    output_file = tmp_path / "out.jsonl"

    runner.invoke(
        app,
        [
            "export-training-data",
            "--run-id", "run-1",
            "--output", str(output_file),
            "--db-path", str(db_path),
        ],
    )

    lines = output_file.read_text(encoding="utf-8").strip().splitlines()
    for line in lines:
        parsed = json.loads(line)
        assert isinstance(parsed, dict)
        assert "run_id" in parsed
        assert "scenario" in parsed
        assert "strategy" in parsed
        assert "score" in parsed


def test_cli_db_override_uses_matching_artifact_roots(tmp_path: Path) -> None:
    _, artifacts, db_path = _setup_db(tmp_path)
    artifacts.write_playbook("grid_ctf", "## Guide\nUse the local temp artifacts.")
    artifacts.write_hints("grid_ctf", "Temp hint.")
    output_file = tmp_path / "out.jsonl"

    result = runner.invoke(
        app,
        [
            "export-training-data",
            "--run-id", "run-1",
            "--output", str(output_file),
            "--db-path", str(db_path),
        ],
    )
    assert result.exit_code == 0, result.output

    first = json.loads(output_file.read_text(encoding="utf-8").splitlines()[0])
    assert "local temp artifacts" in first["context"]["playbook"]
    assert "Temp hint." in first["context"]["hints"]


def test_cli_error_when_no_run_id_or_scenario(tmp_path: Path) -> None:
    _, _, db_path = _setup_db(tmp_path)
    output_file = tmp_path / "out.jsonl"

    result = runner.invoke(
        app,
        [
            "export-training-data",
            "--output", str(output_file),
            "--db-path", str(db_path),
        ],
    )
    assert result.exit_code != 0


def test_cli_kept_only_flag(tmp_path: Path) -> None:
    _, _, db_path = _setup_db(tmp_path)
    output_file = tmp_path / "out.jsonl"

    result = runner.invoke(
        app,
        [
            "export-training-data",
            "--run-id", "run-1",
            "--kept-only",
            "--output", str(output_file),
            "--db-path", str(db_path),
        ],
    )
    assert result.exit_code == 0, result.output

    lines = output_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["gate_decision"] == "advance"


def test_cli_include_matches_flag(tmp_path: Path) -> None:
    _, _, db_path = _setup_db(tmp_path)
    output_file = tmp_path / "out.jsonl"

    result = runner.invoke(
        app,
        [
            "export-training-data",
            "--run-id", "run-1",
            "--include-matches",
            "--output", str(output_file),
            "--db-path", str(db_path),
        ],
    )
    assert result.exit_code == 0, result.output

    lines = output_file.read_text(encoding="utf-8").strip().splitlines()
    # 2 training records + 1 match record (gen 1 has 1 match)
    assert len(lines) == 3
    # The match record should have a "seed" field
    match_lines = [json.loads(line) for line in lines if "seed" in json.loads(line)]
    assert len(match_lines) == 1
    assert match_lines[0]["seed"] == 42
