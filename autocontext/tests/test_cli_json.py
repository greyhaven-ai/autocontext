"""Tests for AC-221: agent-friendly structured CLI output and wait semantics."""
from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from autocontext.cli import app
from autocontext.storage.sqlite_store import SQLiteStore

runner = CliRunner()
MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _make_settings(tmp_path: Path):
    """Build AppSettings pointing at a temp workspace."""
    from autocontext.config.settings import AppSettings

    return AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        event_stream_path=tmp_path / "events.ndjson",
    )


def _setup_db(tmp_path: Path) -> tuple[SQLiteStore, Path]:
    """Create a test DB with migrations applied."""
    db_path = tmp_path / "runs" / "autocontext.sqlite3"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = SQLiteStore(db_path)
    if MIGRATIONS_DIR.exists():
        db.migrate(MIGRATIONS_DIR)
    return db, db_path


# ---------------------------------------------------------------------------
# 1. run --json
# ---------------------------------------------------------------------------


class TestRunJson:
    def test_run_json_success(self, tmp_path: Path) -> None:
        """run --json should output valid JSON with RunSummary fields."""
        from autocontext.loop.generation_runner import RunSummary

        mock_summary = RunSummary(
            run_id="test-run-001",
            scenario="grid_ctf",
            generations_executed=3,
            best_score=0.75,
            current_elo=1050.0,
        )
        mock_runner_instance = MagicMock()
        mock_runner_instance.run.return_value = mock_summary

        with patch("autocontext.cli._runner", return_value=mock_runner_instance):
            result = runner.invoke(app, ["run", "--json", "--scenario", "grid_ctf", "--gens", "1"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output.strip())
        assert data["run_id"] == "test-run-001"
        assert data["scenario"] == "grid_ctf"

    def test_run_json_has_all_fields(self, tmp_path: Path) -> None:
        """run --json output should contain all RunSummary fields."""
        from autocontext.loop.generation_runner import RunSummary

        mock_summary = RunSummary(
            run_id="run-full",
            scenario="othello",
            generations_executed=5,
            best_score=0.9,
            current_elo=1200.0,
        )
        mock_runner_instance = MagicMock()
        mock_runner_instance.run.return_value = mock_summary

        with patch("autocontext.cli._runner", return_value=mock_runner_instance):
            result = runner.invoke(app, ["run", "--json", "--scenario", "othello", "--gens", "5"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output.strip())
        expected_keys = {"run_id", "scenario", "generations_executed", "best_score", "current_elo"}
        assert expected_keys <= set(data.keys())
        assert data["generations_executed"] == 5
        assert data["best_score"] == 0.9
        assert data["current_elo"] == 1200.0

    def test_run_json_error_writes_to_stderr(self) -> None:
        """run --json failures should emit structured stderr and exit 1."""
        mock_runner_instance = MagicMock()
        mock_runner_instance.run.side_effect = RuntimeError("run exploded")

        with patch("autocontext.cli._runner", return_value=mock_runner_instance):
            result = runner.invoke(app, ["run", "--json", "--scenario", "grid_ctf"])

        assert result.exit_code == 1
        error_data = json.loads(result.stderr.strip())
        assert error_data["error"] == "run exploded"

    def test_run_json_rejects_serve_mode(self) -> None:
        """run --json --serve should fail because interactive mode is not machine-readable."""
        result = runner.invoke(app, ["run", "--json", "--serve", "--scenario", "grid_ctf"])

        assert result.exit_code == 2
        error_data = json.loads(result.stderr.strip())
        assert "--json cannot be used with --serve" in error_data["error"]


# ---------------------------------------------------------------------------
# 2. resume --json
# ---------------------------------------------------------------------------


class TestResumeJson:
    def test_resume_json_success(self) -> None:
        """resume --json should output valid JSON with RunSummary fields."""
        from autocontext.loop.generation_runner import RunSummary

        mock_summary = RunSummary(
            run_id="resume-001",
            scenario="grid_ctf",
            generations_executed=2,
            best_score=0.6,
            current_elo=1020.0,
        )
        mock_runner_instance = MagicMock()
        mock_runner_instance.run.return_value = mock_summary

        with patch("autocontext.cli._runner", return_value=mock_runner_instance):
            result = runner.invoke(app, ["resume", "resume-001", "--json", "--scenario", "grid_ctf"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output.strip())
        assert data["run_id"] == "resume-001"
        assert data["scenario"] == "grid_ctf"
        assert data["generations_executed"] == 2

    def test_resume_json_error_writes_to_stderr(self) -> None:
        """resume --json failures should emit structured stderr and exit 1."""
        mock_runner_instance = MagicMock()
        mock_runner_instance.run.side_effect = RuntimeError("resume exploded")

        with patch("autocontext.cli._runner", return_value=mock_runner_instance):
            result = runner.invoke(app, ["resume", "resume-001", "--json"])

        assert result.exit_code == 1
        error_data = json.loads(result.stderr.strip())
        assert error_data["error"] == "resume exploded"


# ---------------------------------------------------------------------------
# 3. list --json
# ---------------------------------------------------------------------------


class TestListJson:
    def test_list_json_empty(self, tmp_path: Path) -> None:
        """list --json with no runs should return empty array."""
        settings = _make_settings(tmp_path)
        _setup_db(tmp_path)

        with patch("autocontext.cli.load_settings", return_value=settings):
            result = runner.invoke(app, ["list", "--json"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output.strip())
        assert data == []

    def test_list_json_bootstraps_fresh_workspace(self, tmp_path: Path) -> None:
        """list --json should not crash when the workspace DB has never been initialized."""
        settings = _make_settings(tmp_path)

        with patch("autocontext.cli.load_settings", return_value=settings):
            result = runner.invoke(app, ["list", "--json"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output.strip())
        assert data == []

    def test_list_json_populated(self, tmp_path: Path) -> None:
        """list --json with seeded runs should return list of dicts."""
        settings = _make_settings(tmp_path)
        db, _ = _setup_db(tmp_path)
        db.create_run("run-a", scenario="grid_ctf", generations=3, executor_mode="local")
        db.create_run("run-b", scenario="othello", generations=1, executor_mode="local")

        with patch("autocontext.cli.load_settings", return_value=settings):
            result = runner.invoke(app, ["list", "--json"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output.strip())
        assert isinstance(data, list)
        assert len(data) == 2
        # Check that the expected fields exist
        for row in data:
            assert "run_id" in row
            assert "scenario" in row
            assert "status" in row

    def test_list_json_is_valid_json(self, tmp_path: Path) -> None:
        """list --json output should be parseable as JSON without error."""
        settings = _make_settings(tmp_path)
        _setup_db(tmp_path)

        with patch("autocontext.cli.load_settings", return_value=settings):
            result = runner.invoke(app, ["list", "--json"])

        assert result.exit_code == 0
        # Should not raise
        json.loads(result.output.strip())


# ---------------------------------------------------------------------------
# 4. status --json
# ---------------------------------------------------------------------------


class TestStatusJson:
    def test_status_json_success(self, tmp_path: Path) -> None:
        """status --json with generations should return structured data."""
        settings = _make_settings(tmp_path)
        db, _ = _setup_db(tmp_path)
        db.create_run("run-status", scenario="grid_ctf", generations=3, executor_mode="local")
        db.upsert_generation(
            run_id="run-status",
            generation_index=1,
            mean_score=0.4,
            best_score=0.5,
            elo=1010.0,
            wins=2,
            losses=1,
            gate_decision="advance",
            status="completed",
        )

        with patch("autocontext.cli.load_settings", return_value=settings):
            result = runner.invoke(app, ["status", "run-status", "--json"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output.strip())
        assert data["run_id"] == "run-status"
        assert isinstance(data["generations"], list)
        assert len(data["generations"]) == 1
        gen = data["generations"][0]
        assert gen["generation"] == 1
        assert gen["mean_score"] == 0.4
        assert gen["gate_decision"] == "advance"

    def test_status_json_no_generations(self, tmp_path: Path) -> None:
        """status --json for a run with no generations should return empty array."""
        settings = _make_settings(tmp_path)
        db, _ = _setup_db(tmp_path)
        db.create_run("run-empty", scenario="grid_ctf", generations=1, executor_mode="local")

        with patch("autocontext.cli.load_settings", return_value=settings):
            result = runner.invoke(app, ["status", "run-empty", "--json"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output.strip())
        assert data["run_id"] == "run-empty"
        assert data["generations"] == []

    def test_status_json_bootstraps_fresh_workspace(self, tmp_path: Path) -> None:
        """status --json should not crash before the workspace DB is initialized."""
        settings = _make_settings(tmp_path)

        with patch("autocontext.cli.load_settings", return_value=settings):
            result = runner.invoke(app, ["status", "missing-run", "--json"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output.strip())
        assert data == {"run_id": "missing-run", "generations": []}

    def test_status_json_is_valid_json(self, tmp_path: Path) -> None:
        """status --json output should be parseable as JSON without error."""
        settings = _make_settings(tmp_path)
        db, _ = _setup_db(tmp_path)
        db.create_run("run-valid", scenario="grid_ctf", generations=1, executor_mode="local")

        with patch("autocontext.cli.load_settings", return_value=settings):
            result = runner.invoke(app, ["status", "run-valid", "--json"])

        assert result.exit_code == 0
        json.loads(result.output.strip())


# ---------------------------------------------------------------------------
# 5. train --json
# ---------------------------------------------------------------------------


class TestTrainJson:
    def test_train_json_success(self) -> None:
        """train --json should output structured training result."""
        from autocontext.training.runner import TrainingResult

        mock_result = TrainingResult(
            scenario="grid_ctf",
            total_experiments=5,
            kept_count=3,
            discarded_count=2,
            best_score=0.8,
            best_experiment_index=2,
            checkpoint_path=None,
        )

        with patch("autocontext.cli._run_training", return_value=mock_result):
            result = runner.invoke(app, ["train", "--json", "--scenario", "grid_ctf"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output.strip())
        assert data["scenario"] == "grid_ctf"
        assert data["total_experiments"] == 5
        assert data["kept_count"] == 3
        assert data["discarded_count"] == 2
        assert data["best_score"] == 0.8
        assert data["checkpoint_path"] is None

    def test_train_json_error(self) -> None:
        """train --json on error should exit 1."""
        with patch("autocontext.cli._run_training", side_effect=RuntimeError("training exploded")):
            result = runner.invoke(app, ["train", "--json", "--scenario", "grid_ctf"])

        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# 6. export --json
# ---------------------------------------------------------------------------


class TestExportJson:
    def test_export_json_success(self, tmp_path: Path) -> None:
        """export --json should output metadata about the exported package."""
        from autocontext.storage.artifacts import ArtifactStore

        db, db_path = _setup_db(tmp_path)
        artifacts = ArtifactStore(
            runs_root=tmp_path / "runs",
            knowledge_root=tmp_path / "knowledge",
            skills_root=tmp_path / "skills",
            claude_skills_path=tmp_path / ".claude" / "skills",
        )
        artifacts.write_playbook("grid_ctf", "## Playbook")
        artifacts.write_hints("grid_ctf", "Scout borders")
        artifacts.write_harness("grid_ctf", "bounds_check", "def validate(s): return True\n")
        db.create_run("test_run", scenario="grid_ctf", generations=1, executor_mode="local")
        db.mark_run_completed("test_run")

        output_path = tmp_path / "export.json"
        result = runner.invoke(app, [
            "export", "--json",
            "--scenario", "grid_ctf",
            "--output", str(output_path),
            "--db-path", str(db_path),
            "--knowledge-root", str(tmp_path / "knowledge"),
            "--skills-root", str(tmp_path / "skills"),
            "--claude-skills-path", str(tmp_path / ".claude" / "skills"),
        ])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output.strip())
        assert data["scenario"] == "grid_ctf"
        assert "output_path" in data
        assert "best_score" in data
        assert "lessons_count" in data
        assert "harness_count" in data

    def test_export_json_missing_scenario(self, tmp_path: Path) -> None:
        """export --json for a missing scenario should exit non-zero."""
        _setup_db(tmp_path)
        db_path = tmp_path / "runs" / "autocontext.sqlite3"

        result = runner.invoke(app, [
            "export", "--json",
            "--scenario", "nonexistent",
            "--db-path", str(db_path),
            "--knowledge-root", str(tmp_path / "knowledge"),
            "--skills-root", str(tmp_path / "skills"),
            "--claude-skills-path", str(tmp_path / ".claude" / "skills"),
        ])

        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# 7. solve --json
# ---------------------------------------------------------------------------


class TestSolveJson:
    def test_solve_json_success(self, tmp_path: Path) -> None:
        """solve --json should output structured solve results."""
        from autocontext.knowledge.export import SkillPackage
        from autocontext.knowledge.solver import SolveJob

        settings = _make_settings(tmp_path)
        pkg = SkillPackage(
            scenario_name="grid_ctf",
            display_name="Grid Ctf",
            description="Solve result",
            playbook="## Playbook",
            lessons=["Scout lanes"],
            best_strategy={"aggression": 0.6},
            best_score=0.81,
            best_elo=1512.0,
            hints="Protect home base",
        )
        job = SolveJob(
            job_id="solve_1234",
            description="Design a strategy",
            scenario_name="grid_ctf",
            status="completed",
            generations=2,
            progress=2,
            result=pkg,
        )

        with (
            patch("autocontext.cli.load_settings", return_value=settings),
            patch("autocontext.knowledge.solver.SolveManager.solve_sync", return_value=job),
        ):
            result = runner.invoke(app, ["solve", "--description", "Design a strategy", "--gens", "2", "--json"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output.strip())
        assert data["job_id"] == "solve_1234"
        assert data["status"] == "completed"
        assert data["scenario_name"] == "grid_ctf"
        assert data["generations"] == 2
        assert data["progress"] == 2
        assert data["result"]["scenario_name"] == "grid_ctf"

    def test_solve_json_writes_output_file(self, tmp_path: Path) -> None:
        """solve --json --output should write the package JSON to disk."""
        from autocontext.knowledge.export import SkillPackage
        from autocontext.knowledge.solver import SolveJob

        settings = _make_settings(tmp_path)
        pkg = SkillPackage(
            scenario_name="grid_ctf",
            display_name="Grid Ctf",
            description="Solve result",
            playbook="## Playbook",
            lessons=["Scout lanes"],
            best_strategy={"aggression": 0.6},
            best_score=0.81,
            best_elo=1512.0,
            hints="Protect home base",
        )
        job = SolveJob(
            job_id="solve_5678",
            description="Design a strategy",
            scenario_name="grid_ctf",
            status="completed",
            generations=3,
            progress=3,
            result=pkg,
        )
        output_path = tmp_path / "exports" / "solve.json"

        with (
            patch("autocontext.cli.load_settings", return_value=settings),
            patch("autocontext.knowledge.solver.SolveManager.solve_sync", return_value=job),
        ):
            result = runner.invoke(
                app,
                [
                    "solve",
                    "--description",
                    "Design a strategy",
                    "--gens",
                    "3",
                    "--output",
                    str(output_path),
                    "--json",
                ],
            )

        assert result.exit_code == 0, result.output
        data = json.loads(result.output.strip())
        assert data["output_path"] == str(output_path)
        assert output_path.exists()
        written = json.loads(output_path.read_text(encoding="utf-8"))
        assert written["scenario_name"] == "grid_ctf"
        assert written["best_strategy"]["aggression"] == 0.6

    def test_solve_json_failure_writes_to_stderr(self, tmp_path: Path) -> None:
        """solve --json failures should emit structured stderr and exit 1."""
        from autocontext.knowledge.solver import SolveJob

        settings = _make_settings(tmp_path)
        job = SolveJob(
            job_id="solve_fail",
            description="Broken solve",
            status="failed",
            generations=1,
            progress=0,
            error="solver exploded",
        )

        with (
            patch("autocontext.cli.load_settings", return_value=settings),
            patch("autocontext.knowledge.solver.SolveManager.solve_sync", return_value=job),
        ):
            result = runner.invoke(app, ["solve", "--description", "Broken solve", "--json"])

        assert result.exit_code == 1
        error_data = json.loads(result.stderr.strip())
        assert error_data["error"] == "solver exploded"


# ---------------------------------------------------------------------------
# 8. import-package --json
# ---------------------------------------------------------------------------


class TestImportPackageJson:
    def test_import_json_success(self, tmp_path: Path) -> None:
        """import-package --json should output structured import result."""
        from autocontext.knowledge.package import StrategyPackage

        _setup_db(tmp_path)
        db_path = tmp_path / "runs" / "autocontext.sqlite3"

        pkg = StrategyPackage(
            scenario_name="grid_ctf",
            playbook="## Imported playbook",
            hints="Be careful",
            harness={"bounds_check": "def validate(s): return True\n"},
        )
        pkg_path = tmp_path / "pkg.json"
        pkg.to_file(pkg_path)

        result = runner.invoke(app, [
            "import-package", str(pkg_path), "--json",
            "--db-path", str(db_path),
            "--knowledge-root", str(tmp_path / "knowledge"),
            "--skills-root", str(tmp_path / "skills"),
            "--claude-skills-path", str(tmp_path / ".claude" / "skills"),
        ])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output.strip())
        assert data["scenario_name"] == "grid_ctf"
        assert data["playbook_written"] is True
        assert data["hints_written"] is True
        assert "conflict_policy" in data

    def test_import_json_missing_file(self, tmp_path: Path) -> None:
        """import-package --json with missing file should exit 1."""
        result = runner.invoke(app, [
            "import-package", str(tmp_path / "missing.json"), "--json",
        ])

        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# 9. ecosystem --json
# ---------------------------------------------------------------------------


class TestEcosystemJson:
    def test_ecosystem_json_success(self) -> None:
        """ecosystem --json should output runs and trajectory."""
        from autocontext.loop.ecosystem_runner import EcosystemSummary
        from autocontext.loop.generation_runner import RunSummary

        mock_summaries = [
            RunSummary(run_id="eco-1", scenario="grid_ctf", generations_executed=2, best_score=0.6, current_elo=1010.0),
            RunSummary(run_id="eco-2", scenario="grid_ctf", generations_executed=2, best_score=0.7, current_elo=1030.0),
        ]

        mock_eco_summary = EcosystemSummary(
            run_summaries=mock_summaries,
            scenario="grid_ctf",
            cycles=1,
        )

        mock_eco_runner_instance = MagicMock()
        mock_eco_runner_instance.run.return_value = mock_eco_summary
        mock_eco_runner_instance.migrate = MagicMock()

        with (
            patch("autocontext.cli.load_settings", return_value=MagicMock()),
            patch("autocontext.loop.ecosystem_runner.EcosystemRunner", return_value=mock_eco_runner_instance),
        ):
            result = runner.invoke(app, ["ecosystem", "--json", "--scenario", "grid_ctf", "--cycles", "1"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output.strip())
        assert "runs" in data
        assert "trajectory" in data
        assert len(data["runs"]) == 2
        assert len(data["trajectory"]) == 2
        assert data["trajectory"][0]["run_id"] == "eco-1"


# ---------------------------------------------------------------------------
# 10. wait --json
# ---------------------------------------------------------------------------


class TestWaitJson:
    def test_wait_json_timeout(self, tmp_path: Path) -> None:
        """wait --json should output fired=false and exit 1 on timeout."""
        settings = _make_settings(tmp_path)
        _setup_db(tmp_path)

        with (
            patch("autocontext.cli.load_settings", return_value=settings),
            patch("autocontext.cli.SQLiteStore") as MockStoreClass,
            patch("autocontext.cli.time.sleep"),
        ):
            mock_store = MockStoreClass.return_value
            mock_store.get_monitor_condition.return_value = {"id": "cond-1", "name": "test-cond"}
            mock_store.migrate = MagicMock()
            mock_store.get_latest_monitor_alert.return_value = None

            result = runner.invoke(app, ["wait", "cond-1", "--timeout", "0.1", "--json"])

        assert result.exit_code == 1
        data = json.loads(result.output.strip())
        assert data["fired"] is False
        assert data["condition_id"] == "cond-1"
        assert data["timeout_seconds"] == 0.1

    def test_wait_json_fired(self, tmp_path: Path) -> None:
        """wait --json should output fired=true and exit 0 when alert fires."""
        settings = _make_settings(tmp_path)
        _setup_db(tmp_path)

        with (
            patch("autocontext.cli.load_settings", return_value=settings),
            patch("autocontext.cli.SQLiteStore") as MockStoreClass,
            patch("autocontext.cli.time.sleep"),
        ):
            mock_store = MockStoreClass.return_value
            mock_store.get_monitor_condition.return_value = {"id": "cond-2", "name": "test-cond-2"}
            mock_store.migrate = MagicMock()
            mock_store.get_latest_monitor_alert.side_effect = [
                None,
                {
                    "id": "alert-1",
                    "detail": "Score exceeded 0.8",
                    "fired_at": "2026-01-01T00:00:00Z",
                },
            ]

            result = runner.invoke(app, ["wait", "cond-2", "--timeout", "5", "--json"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output.strip())
        assert data["fired"] is True
        assert data["condition_id"] == "cond-2"
        assert data["alert"] is not None

    def test_wait_json_unknown_condition(self, tmp_path: Path) -> None:
        """wait --json for an unknown condition should exit 1."""
        settings = _make_settings(tmp_path)

        with (
            patch("autocontext.cli.load_settings", return_value=settings),
            patch("autocontext.cli.SQLiteStore") as MockStoreClass,
        ):
            mock_store = MockStoreClass.return_value
            mock_store.get_monitor_condition.return_value = None
            mock_store.migrate = MagicMock()

            result = runner.invoke(app, ["wait", "nonexistent-cond", "--timeout", "1", "--json"])

        assert result.exit_code == 1

    def test_wait_human_timeout_message(self, tmp_path: Path) -> None:
        """wait without --json should print human-readable timeout message."""
        settings = _make_settings(tmp_path)

        with (
            patch("autocontext.cli.load_settings", return_value=settings),
            patch("autocontext.cli.SQLiteStore") as MockStoreClass,
            patch("autocontext.cli.time.sleep"),
        ):
            mock_store = MockStoreClass.return_value
            mock_store.get_monitor_condition.return_value = {"id": "cond-h", "name": "human-test"}
            mock_store.migrate = MagicMock()
            mock_store.get_latest_monitor_alert.return_value = None

            result = runner.invoke(app, ["wait", "cond-h", "--timeout", "0.1"])

        assert result.exit_code == 1
        assert "Timed out" in result.output

    def test_wait_human_fired_message(self, tmp_path: Path) -> None:
        """wait without --json should print human-readable fired message."""
        settings = _make_settings(tmp_path)

        with (
            patch("autocontext.cli.load_settings", return_value=settings),
            patch("autocontext.cli.SQLiteStore") as MockStoreClass,
            patch("autocontext.cli.time.sleep"),
        ):
            mock_store = MockStoreClass.return_value
            mock_store.get_monitor_condition.return_value = {"id": "cond-f", "name": "fire-test"}
            mock_store.migrate = MagicMock()
            mock_store.get_latest_monitor_alert.side_effect = [
                None,
                {
                    "id": "alert-h",
                    "detail": "Score exceeded threshold",
                },
            ]

            result = runner.invoke(app, ["wait", "cond-f", "--timeout", "5"])

        assert result.exit_code == 0, result.output
        assert "Alert fired" in result.output


# ---------------------------------------------------------------------------
# 10. Error stderr tests
# ---------------------------------------------------------------------------


class TestErrorStderr:
    def test_train_json_error_writes_to_stderr(self) -> None:
        """train --json errors should write JSON error to stderr."""
        with patch("autocontext.cli._run_training", side_effect=RuntimeError("boom")):
            result = runner.invoke(app, ["train", "--json", "--scenario", "grid_ctf"])

        assert result.exit_code == 1
        # CliRunner captures stderr in result.stderr (click >= 8.2)
        error_data = json.loads(result.stderr.strip())
        assert error_data["error"] == "boom"

    def test_import_json_missing_file_writes_to_stderr(self, tmp_path: Path) -> None:
        """import-package --json with missing file should write error JSON to stderr."""
        missing = tmp_path / "no_such_file.json"

        result = runner.invoke(app, [
            "import-package", str(missing), "--json",
        ])

        assert result.exit_code == 1
        error_data = json.loads(result.stderr.strip())
        assert "error" in error_data
        assert "File not found" in error_data["error"]


# ---------------------------------------------------------------------------
# 11. Flag presence in help
# ---------------------------------------------------------------------------


class TestFlagPresence:
    def test_json_flag_in_run_help(self) -> None:
        """--json flag should appear in run command help."""
        result = runner.invoke(app, ["run", "--help"])
        assert "--json" in _strip_ansi(result.output)

    def test_json_flag_in_list_help(self) -> None:
        """--json flag should appear in list command help."""
        result = runner.invoke(app, ["list", "--help"])
        assert "--json" in _strip_ansi(result.output)
