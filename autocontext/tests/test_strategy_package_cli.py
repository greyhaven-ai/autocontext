"""Tests for AC-189: autoctx export / autoctx import CLI commands."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from autocontext.cli import app
from autocontext.storage.artifacts import ArtifactStore
from autocontext.storage.sqlite_store import SQLiteStore

runner = CliRunner()


def _setup_db_and_artifacts(tmp_path: Path) -> tuple[SQLiteStore, ArtifactStore, Path]:
    """Create test DB with migrations and artifacts."""
    db_path = tmp_path / "runs" / "autocontext.sqlite3"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = SQLiteStore(db_path)
    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    if migrations_dir.exists():
        db.migrate(migrations_dir)
    artifacts = ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
    )
    return db, artifacts, db_path


def _seed_scenario(db: SQLiteStore, artifacts: ArtifactStore, scenario: str = "grid_ctf") -> None:
    """Seed a minimal scenario with playbook and a completed run."""
    artifacts.write_playbook(scenario, "## Test Playbook\n\nBe aggressive.")
    artifacts.write_hints(scenario, "Scout the borders.")
    artifacts.write_harness(scenario, "bounds_check", "def validate(s): return True\n")
    db.create_run("test_run_001", scenario=scenario, generations=3, executor_mode="local")
    db.mark_run_completed("test_run_001")


class TestExportCommand:
    def test_export_creates_json_file(self, tmp_path: Path) -> None:
        db, artifacts, db_path = _setup_db_and_artifacts(tmp_path)
        _seed_scenario(db, artifacts)
        output = tmp_path / "export.json"

        result = runner.invoke(app, [
            "export",
            "--scenario", "grid_ctf",
            "--output", str(output),
            "--db-path", str(db_path),
            "--knowledge-root", str(tmp_path / "knowledge"),
            "--skills-root", str(tmp_path / "skills"),
            "--claude-skills-path", str(tmp_path / ".claude" / "skills"),
        ])
        assert result.exit_code == 0, result.output
        assert output.exists()
        data = json.loads(output.read_text(encoding="utf-8"))
        assert data["scenario_name"] == "grid_ctf"
        assert data["format_version"] == 1

    def test_export_default_filename(self, tmp_path: Path) -> None:
        db, artifacts, db_path = _setup_db_and_artifacts(tmp_path)
        _seed_scenario(db, artifacts)

        result = runner.invoke(app, [
            "export",
            "--scenario", "grid_ctf",
            "--db-path", str(db_path),
            "--knowledge-root", str(tmp_path / "knowledge"),
            "--skills-root", str(tmp_path / "skills"),
            "--claude-skills-path", str(tmp_path / ".claude" / "skills"),
        ])
        assert result.exit_code == 0, result.output
        default_file = Path("grid_ctf_package.json")
        if default_file.exists():
            default_file.unlink()

    def test_export_unknown_scenario_fails(self, tmp_path: Path) -> None:
        db, artifacts, db_path = _setup_db_and_artifacts(tmp_path)

        result = runner.invoke(app, [
            "export",
            "--scenario", "nonexistent_scenario",
            "--output", str(tmp_path / "out.json"),
            "--db-path", str(db_path),
            "--knowledge-root", str(tmp_path / "knowledge"),
            "--skills-root", str(tmp_path / "skills"),
            "--claude-skills-path", str(tmp_path / ".claude" / "skills"),
        ])
        assert result.exit_code != 0


class TestImportCommand:
    def _write_package_file(self, tmp_path: Path, **overrides: object) -> Path:
        """Write a minimal valid package JSON file."""
        from autocontext.knowledge.package import StrategyPackage

        defaults = {
            "scenario_name": "grid_ctf",
            "playbook": "## Imported Playbook",
            "hints": "Imported hints.",
            "harness": {"imported_validator": "def check(s): return True\n"},
        }
        defaults.update(overrides)
        pkg = StrategyPackage(**defaults)
        pkg_file = tmp_path / "package.json"
        pkg.to_file(pkg_file)
        return pkg_file

    def test_import_from_file(self, tmp_path: Path) -> None:
        _, _, db_path = _setup_db_and_artifacts(tmp_path)
        pkg_file = self._write_package_file(tmp_path)

        result = runner.invoke(app, [
            "import-package",
            str(pkg_file),
            "--db-path", str(db_path),
            "--knowledge-root", str(tmp_path / "knowledge"),
            "--skills-root", str(tmp_path / "skills"),
            "--claude-skills-path", str(tmp_path / ".claude" / "skills"),
        ])
        assert result.exit_code == 0, result.output
        # Verify playbook was written
        playbook_path = tmp_path / "knowledge" / "grid_ctf" / "playbook.md"
        assert playbook_path.exists()
        assert "Imported Playbook" in playbook_path.read_text(encoding="utf-8")

    def test_import_with_scenario_override(self, tmp_path: Path) -> None:
        _, _, db_path = _setup_db_and_artifacts(tmp_path)
        pkg_file = self._write_package_file(tmp_path, scenario_name="grid_ctf")

        result = runner.invoke(app, [
            "import-package",
            str(pkg_file),
            "--scenario", "othello",
            "--db-path", str(db_path),
            "--knowledge-root", str(tmp_path / "knowledge"),
            "--skills-root", str(tmp_path / "skills"),
            "--claude-skills-path", str(tmp_path / ".claude" / "skills"),
        ])
        assert result.exit_code == 0, result.output
        # Should have written to othello, not grid_ctf
        playbook_path = tmp_path / "knowledge" / "othello" / "playbook.md"
        assert playbook_path.exists()

    def test_import_with_conflict_overwrite(self, tmp_path: Path) -> None:
        _, _, db_path = _setup_db_and_artifacts(tmp_path)
        # Pre-populate existing playbook
        knowledge_root = tmp_path / "knowledge"
        (knowledge_root / "grid_ctf").mkdir(parents=True)
        (knowledge_root / "grid_ctf" / "playbook.md").write_text("## Old playbook\n", encoding="utf-8")

        pkg_file = self._write_package_file(tmp_path, playbook="## New playbook")

        result = runner.invoke(app, [
            "import-package",
            str(pkg_file),
            "--conflict", "overwrite",
            "--db-path", str(db_path),
            "--knowledge-root", str(knowledge_root),
            "--skills-root", str(tmp_path / "skills"),
            "--claude-skills-path", str(tmp_path / ".claude" / "skills"),
        ])
        assert result.exit_code == 0, result.output
        content = (knowledge_root / "grid_ctf" / "playbook.md").read_text(encoding="utf-8")
        assert "New playbook" in content

    def test_import_invalid_json_fails(self, tmp_path: Path) -> None:
        _, _, db_path = _setup_db_and_artifacts(tmp_path)
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not json at all", encoding="utf-8")

        result = runner.invoke(app, [
            "import-package",
            str(bad_file),
            "--db-path", str(db_path),
            "--knowledge-root", str(tmp_path / "knowledge"),
            "--skills-root", str(tmp_path / "skills"),
            "--claude-skills-path", str(tmp_path / ".claude" / "skills"),
        ])
        assert result.exit_code != 0

    def test_import_missing_file_fails(self, tmp_path: Path) -> None:
        _, _, db_path = _setup_db_and_artifacts(tmp_path)
        result = runner.invoke(app, [
            "import-package",
            str(tmp_path / "does_not_exist.json"),
            "--db-path", str(db_path),
            "--knowledge-root", str(tmp_path / "knowledge"),
            "--skills-root", str(tmp_path / "skills"),
            "--claude-skills-path", str(tmp_path / ".claude" / "skills"),
        ])
        assert result.exit_code != 0

    def test_import_restores_exportable_snapshot(self, tmp_path: Path) -> None:
        _, _, db_path = _setup_db_and_artifacts(tmp_path)
        pkg_file = self._write_package_file(
            tmp_path,
            best_strategy={"aggression": 0.8},
            best_score=0.91,
            best_elo=1725.0,
            metadata={"completed_runs": 4, "has_snapshot": True},
        )

        result = runner.invoke(app, [
            "import-package",
            str(pkg_file),
            "--db-path", str(db_path),
            "--knowledge-root", str(tmp_path / "knowledge"),
            "--skills-root", str(tmp_path / "skills"),
            "--claude-skills-path", str(tmp_path / ".claude" / "skills"),
        ])
        assert result.exit_code == 0, result.output

        export_file = tmp_path / "roundtrip.json"
        export_result = runner.invoke(app, [
            "export",
            "--scenario", "grid_ctf",
            "--output", str(export_file),
            "--db-path", str(db_path),
            "--knowledge-root", str(tmp_path / "knowledge"),
            "--skills-root", str(tmp_path / "skills"),
            "--claude-skills-path", str(tmp_path / ".claude" / "skills"),
        ])
        assert export_result.exit_code == 0, export_result.output
        data = json.loads(export_file.read_text(encoding="utf-8"))
        assert data["best_strategy"] == {"aggression": 0.8}
        assert data["best_score"] == pytest.approx(0.91)
        assert data["metadata"]["completed_runs"] == 4
