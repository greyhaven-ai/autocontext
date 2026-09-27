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
from autocontext.storage import artifact_store_from_settings
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


def _seed(tmp_path: Path, *, scenario="othello", target=3, executor_mode="local", minimum=1, agent_provider="") -> None:
    store = SQLiteStore(tmp_path / "runs" / "autocontext.sqlite3")
    store.migrate(MIGRATIONS_DIR)
    store.create_run("r1", scenario, target, executor_mode, agent_provider=agent_provider, minimum_generations=minimum)
    store.mark_run_failed("r1")


def _invoke(tmp_path: Path, args: list[str], *, executed: int = 1):
    fake = MagicMock()
    fake.run.return_value = RunSummary(
        run_id="r1", scenario="othello", generations_executed=executed, best_score=0.5, current_elo=1000.0
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
    fake.run.assert_called_once_with(
        scenario_name="othello", generations=3, run_id="r1", minimum_generations=1, allow_runtime_change=False
    )


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


@pytest.mark.parametrize(
    ("seeded", "stored", "current"),
    [
        ({"agent_provider": "deterministic"}, "agent_provider 'deterministic'", "agent_provider 'anthropic'"),
        ({"executor_mode": "monty"}, "executor_mode 'monty'", "executor_mode 'local'"),
    ],
)
@pytest.mark.parametrize("json_output", [True, False])
def test_resume_refuses_a_runtime_change_as_a_usage_error(
    tmp_path: Path, seeded: dict, stored: str, current: str, json_output: bool
) -> None:
    # Resume built the runner from the current settings, so the rest of the run silently used them.
    _seed(tmp_path, **seeded)
    result, _, make_runner = _invoke(tmp_path, ["r1", "--json"] if json_output else ["r1"])
    assert result.exit_code == 2
    error = json.loads(result.stderr)["error"] if json_output else result.stderr
    assert f"run 'r1' was created with {stored}, but the current settings use {current}" in error
    assert "autoctx resume r1 --allow-runtime-change" in error
    make_runner.assert_not_called()


def test_resume_allow_runtime_change_hands_the_switch_to_the_runner(tmp_path: Path) -> None:
    _seed(tmp_path, executor_mode="monty", agent_provider="deterministic")
    result, fake, _ = _invoke(tmp_path, ["r1", "--allow-runtime-change", "--json"])
    assert result.exit_code == 0, result.output
    assert fake.run.call_args.kwargs["allow_runtime_change"] is True


def test_resume_refuses_an_allowed_provider_switch_while_the_context_bundle_pins_the_old_provider(tmp_path: Path) -> None:
    # The runner reopened the run, announced the switch and then failed mid-generation on the scenario's
    # context bundle, which pins the provider its generations were built for.
    _seed(tmp_path, agent_provider="deterministic")
    artifact_store_from_settings(_settings(tmp_path)).ensure_context_bundle_baseline(
        "othello", evaluator_epoch="seeded", routing_config={"agent_provider": "deterministic"}
    )
    result, _, make_runner = _invoke(tmp_path, ["r1", "--allow-runtime-change", "--json"])
    assert result.exit_code == 2
    error = json.loads(result.stderr)["error"]
    assert "run 'r1' cannot switch to agent_provider 'anthropic'" in error
    assert "the active context bundle of scenario 'othello' pins agent_provider 'deterministic'" in error
    assert "AUTOCONTEXT_AGENT_PROVIDER=deterministic" in error
    make_runner.assert_not_called()


@pytest.mark.parametrize("allow", [False, True])
def test_resume_of_a_stopped_run_reports_it_terminal_rather_than_its_runtime(tmp_path: Path, allow: bool) -> None:
    # The runtime refusal came first and suggested a setting or --allow-runtime-change, and each of those then
    # failed because a stopped run is terminal.
    _seed(tmp_path, executor_mode="monty", agent_provider="openai-compatible")
    store = SQLiteStore(tmp_path / "runs" / "autocontext.sqlite3")
    store.mark_run_running("r1")
    store.mark_run_stopped("r1")
    settings = _settings(tmp_path).model_copy(update={"agent_provider": "deterministic"})
    artifact_store_from_settings(settings).ensure_context_bundle_baseline(
        "othello", evaluator_epoch="seeded", routing_config={"agent_provider": "openai-compatible"}
    )
    with patch("autocontext.cli.load_settings", return_value=settings):
        result = cli.invoke(app, ["resume", "r1", "--json", *(["--allow-runtime-change"] if allow else [])])
    assert result.exit_code == 1, result.output
    assert json.loads(result.stderr) == {"error": "run 'r1' was stopped and is terminal; start a new run id to continue"}


def test_resume_forwards_the_stored_minimum(tmp_path: Path) -> None:
    _seed(tmp_path, minimum=2)
    result, fake, _ = _invoke(tmp_path, ["r1", "--json"])
    assert result.exit_code == 0, result.output
    assert fake.run.call_args.kwargs["minimum_generations"] == 2


@pytest.mark.parametrize(
    ("status", "message"),
    [
        ("failed", "Resumed r1 with 0 executed generation(s)."),
        ("completed", "Run r1 is already completed; nothing to resume."),
    ],
)
def test_resume_says_already_completed_only_for_a_run_that_was(tmp_path: Path, status: str, message: str) -> None:
    # A failed run with every generation done also executes 0, but resuming it finishes the run; it was not completed.
    _seed(tmp_path, target=1)
    if status == "completed":
        SQLiteStore(tmp_path / "runs" / "autocontext.sqlite3").mark_run_completed("r1")

    result, _, _ = _invoke(tmp_path, ["r1"], executed=0)

    assert result.exit_code == 0, result.output
    assert message in result.stdout


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


@pytest.mark.slow
def test_resume_under_another_executor_exits_2_without_running_a_generation(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Resume ran generation 2 on the current executor while the row still said 'local' (monty, unlike ssh, needs no host).
    monkeypatch.setenv("AUTOCONTEXT_MATCHES_PER_GENERATION", "1")
    first = cli.invoke(app, ["run", "othello", "--iterations", "1", "--run-id", "ex1", "--json", "--skip-preflight"])
    assert first.exit_code == 0, first.output
    db_path = workspace / "runs" / "autocontext.sqlite3"

    def stored() -> tuple:
        conn = sqlite3.connect(db_path)
        try:
            run = conn.execute(
                "SELECT target_generations, status, executor_mode, agent_provider FROM runs WHERE run_id = 'ex1'"
            ).fetchone()
            gens = conn.execute("SELECT generation_index, status FROM generations WHERE run_id = 'ex1' ORDER BY 1").fetchall()
        finally:
            conn.close()
        return run, gens

    before = stored()
    assert before == ((1, "completed", "local", "deterministic"), [(1, "completed")])
    events = (workspace / "runs" / "events.ndjson").read_text(encoding="utf-8")
    monkeypatch.setenv("AUTOCONTEXT_EXECUTOR_MODE", "monty")

    resumed = cli.invoke(app, ["resume", "ex1", "--iterations", "2", "--json"])

    assert resumed.exit_code == 2, resumed.output
    error = json.loads(resumed.stderr)["error"]
    assert "created with executor_mode 'local', but the current settings use executor_mode 'monty'" in error
    assert "AUTOCONTEXT_EXECUTOR_MODE=local" in error
    assert stored() == before
    assert (workspace / "runs" / "events.ndjson").read_text(encoding="utf-8") == events


@pytest.mark.slow
def test_resume_of_a_completed_run_leaves_it_unchanged(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Resuming a finished run reran its post-run tail and reported "Resumed ... with 0 executed generation(s)".
    monkeypatch.setenv("AUTOCONTEXT_MATCHES_PER_GENERATION", "1")
    first = cli.invoke(app, ["run", "othello", "--iterations", "1", "--run-id", "done", "--json", "--skip-preflight"])
    assert first.exit_code == 0, first.output
    events = (workspace / "runs" / "events.ndjson").read_text(encoding="utf-8")

    resumed = cli.invoke(app, ["resume", "done"])

    assert resumed.exit_code == 0, resumed.output
    assert (workspace / "runs" / "events.ndjson").read_text(encoding="utf-8") == events
    assert "Run done is already completed; nothing to resume." in resumed.stdout
