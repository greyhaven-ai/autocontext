"""`autoctx resume` resolves the scenario and target from the stored run."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from autocontext.cli import app
from autocontext.config.settings import AppSettings
from autocontext.loop.generation_pipeline import GenerationPipeline
from autocontext.loop.generation_runner import RunSummary
from autocontext.storage.sqlite_store import SQLiteStore

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"
cli = CliRunner()


def _settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        event_stream_path=tmp_path / "runs" / "events.ndjson",
        audit_log_path=tmp_path / "runs" / "audit.ndjson",
    )


def _seed(tmp_path: Path, *, scenario="othello", target=3, executor_mode="local", minimum=1) -> None:
    store = SQLiteStore(tmp_path / "runs" / "autocontext.sqlite3")
    store.migrate(MIGRATIONS_DIR)
    store.create_run("r1", scenario, target, executor_mode, minimum_generations=minimum)
    store.mark_run_failed("r1")


def _invoke(tmp_path: Path, args: list[str]):
    fake = MagicMock()
    fake.run.return_value = RunSummary(
        run_id="r1", scenario="othello", generations_executed=1, best_score=0.5, current_elo=1000.0
    )
    with (
        patch("autocontext.cli.load_settings", return_value=_settings(tmp_path)),
        patch("autocontext.cli._runner", return_value=fake) as make_runner,
    ):
        result = cli.invoke(app, ["resume", *args])
    return result, fake, make_runner


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in [key for key in os.environ if key.startswith("AUTOCONTEXT_")]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("AUTOCONTEXT_AGENT_PROVIDER", "deterministic")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_resume_defaults_scenario_and_target_to_the_stored_run(tmp_path: Path) -> None:
    _seed(tmp_path)
    result, fake, _ = _invoke(tmp_path, ["r1", "--json"])
    assert result.exit_code == 0, result.output
    fake.run.assert_called_once_with(scenario_name="othello", generations=3, run_id="r1", minimum_generations=1)


def test_resume_rejects_a_different_scenario_as_a_usage_error(tmp_path: Path) -> None:
    _seed(tmp_path)
    result, _, make_runner = _invoke(tmp_path, ["r1", "--scenario", "grid_ctf", "--json"])
    assert result.exit_code == 2
    assert json.loads(result.stderr) == {"error": "run 'r1' belongs to scenario 'othello', not 'grid_ctf'"}
    make_runner.assert_not_called()


def test_resume_text_mode_usage_error_goes_to_stderr(tmp_path: Path) -> None:
    _seed(tmp_path)
    result, _, _ = _invoke(tmp_path, ["r1", "-s", "grid_ctf"])
    assert result.exit_code == 2
    assert result.stderr.startswith("Error: run 'r1' belongs to scenario 'othello'")


def test_resume_accepts_the_matching_scenario(tmp_path: Path) -> None:
    _seed(tmp_path)
    result, fake, _ = _invoke(tmp_path, ["r1", "--scenario", "othello", "--json"])
    assert result.exit_code == 0, result.output
    assert fake.run.call_args.kwargs["scenario_name"] == "othello"


@pytest.mark.parametrize("blank", ["", "   "])
def test_resume_treats_a_blank_scenario_as_omitted(tmp_path: Path, blank: str) -> None:
    _seed(tmp_path)
    result, fake, _ = _invoke(tmp_path, ["r1", "--scenario", blank, "--json"])
    assert result.exit_code == 0, result.output
    assert fake.run.call_args.kwargs["scenario_name"] == "othello"


def test_resume_of_an_unknown_run_fails_without_creating_it(tmp_path: Path) -> None:
    _seed(tmp_path)
    result, _, make_runner = _invoke(tmp_path, ["nope", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.stderr) == {"error": "run 'nope' not found"}
    make_runner.assert_not_called()
    assert SQLiteStore(tmp_path / "runs" / "autocontext.sqlite3").get_run("nope") is None


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["--iterations", "5"], 5),
        (["--gens", "4"], 4),
        (["-g", "4"], 4),
        (["--iterations", "5", "--gens", "4"], 5),
        (["--iterations", "3"], 3),
    ],
)
def test_resume_iterations_sets_an_absolute_target(tmp_path: Path, args: list[str], expected: int) -> None:
    _seed(tmp_path)
    result, fake, _ = _invoke(tmp_path, ["r1", *args, "--json"])
    assert result.exit_code == 0, result.output
    assert fake.run.call_args.kwargs["generations"] == expected


def test_resume_rejects_a_target_below_the_stored_one(tmp_path: Path) -> None:
    _seed(tmp_path)
    result, _, make_runner = _invoke(tmp_path, ["r1", "--iterations", "1", "--json"])
    assert result.exit_code == 2
    assert "targets 3 generations" in json.loads(result.stderr)["error"]
    make_runner.assert_not_called()


@pytest.mark.parametrize("executor_mode", ["agent_task", "artifact_editing", "import"])
def test_resume_refuses_runs_the_generation_loop_did_not_create(tmp_path: Path, executor_mode: str) -> None:
    _seed(tmp_path, executor_mode=executor_mode)
    result, _, make_runner = _invoke(tmp_path, ["r1", "--json"])
    assert result.exit_code == 2
    assert "not created by the generation loop" in json.loads(result.stderr)["error"]
    make_runner.assert_not_called()


def test_resume_forwards_the_stored_minimum(tmp_path: Path) -> None:
    _seed(tmp_path, minimum=2)
    result, fake, _ = _invoke(tmp_path, ["r1", "--json"])
    assert result.exit_code == 0, result.output
    assert fake.run.call_args.kwargs["minimum_generations"] == 2


@pytest.mark.parametrize("variable", ["AUTOCONTEXT_MATCHES_PER_GENERATION", "AUTOCONTEXT_DB_PATH"])
def test_resume_settings_and_database_errors_stay_structured(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    # An invalid integer, or a database path that is a directory.
    monkeypatch.setenv(variable, "abc" if variable.endswith("GENERATION") else str(workspace))
    result = cli.invoke(app, ["resume", "r1", "--json"])
    assert result.exit_code == 1
    error = json.loads(result.stderr)
    assert set(error) == {"error"}
    assert "not found" not in error["error"]


@pytest.mark.slow
def test_resume_finishes_a_failed_run_end_to_end(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = GenerationPipeline.run_generation

    def fail_generation_2(self, ctx):  # type: ignore[no-untyped-def]
        if ctx.generation == 2:
            raise RuntimeError("injected generation 2 failure")
        return original(self, ctx)

    monkeypatch.setattr(GenerationPipeline, "run_generation", fail_generation_2)
    first = cli.invoke(app, ["run", "othello", "--iterations", "2", "--run-id", "demo", "--json", "--skip-preflight"])
    assert first.exit_code == 1, first.output
    monkeypatch.setattr(GenerationPipeline, "run_generation", original)

    resumed = cli.invoke(app, ["resume", "demo", "--json"])

    assert resumed.exit_code == 0, resumed.output
    payload = json.loads(resumed.stdout)
    assert (payload["scenario"], payload["generations_executed"]) == ("othello", 1)
    conn = sqlite3.connect(workspace / "runs" / "autocontext.sqlite3")
    try:
        run = conn.execute("SELECT scenario, target_generations, status FROM runs WHERE run_id = 'demo'").fetchone()
        gens = conn.execute("SELECT generation_index, status FROM generations WHERE run_id = 'demo' ORDER BY 1").fetchall()
        snapshot_scenarios = {row[0] for row in conn.execute("SELECT scenario FROM knowledge_snapshots WHERE run_id = 'demo'")}
    finally:
        conn.close()
    assert run == ("othello", 2, "completed")
    assert gens == [(1, "completed"), (2, "completed")]
    assert snapshot_scenarios <= {"othello"}
    replays = sorted(p.name for p in (workspace / "runs" / "demo" / "generations" / "gen_2" / "replays").glob("*.json"))
    assert replays and all(name.startswith("othello") for name in replays)
    assert not (workspace / "knowledge" / "grid_ctf").exists()

    replay = cli.invoke(app, ["replay", "demo", "--generation", "2"])
    assert replay.exit_code == 0, replay.output
    assert json.loads(replay.stdout)["scenario"] == "othello"
