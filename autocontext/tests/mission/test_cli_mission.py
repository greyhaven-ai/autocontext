"""AC-697 mission CLI subcommand parity tests (slice 4).

Mirrors `ts/tests/cli/mission-command.test.ts` for the create / run
/ status / list / artifacts subcommands. Uses `CliRunner` to invoke
the typer app end-to-end; isolates state by pointing
`AUTOCONTEXT_DB_PATH` + `AUTOCONTEXT_RUNS_ROOT` at per-test
temporary paths via `monkeypatch`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from autocontext.cli import app
from autocontext.cli_mission import (
    MISSION_HELP_TEXT,
    plan_mission_create,
    plan_mission_run,
)


def _env_isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AUTOCONTEXT_DB_PATH", str(tmp_path / "m.sqlite3"))
    monkeypatch.setenv("AUTOCONTEXT_RUNS_ROOT", str(tmp_path / "runs"))


# ---------------------------------------------------------------------------
# planning helpers (pure)
# ---------------------------------------------------------------------------


def test_plan_mission_create_requires_name_and_goal() -> None:
    with pytest.raises(ValueError, match="--name"):
        plan_mission_create(
            mission_type=None,
            name=None,
            goal="goal",
            max_steps=None,
            repo_path=None,
            test_command=None,
            lint_command=None,
            build_command=None,
        )


def test_plan_mission_create_generic_default() -> None:
    plan = plan_mission_create(
        mission_type=None,
        name="x",
        goal="g",
        max_steps=None,
        repo_path=None,
        test_command=None,
        lint_command=None,
        build_command=None,
    )
    assert plan.mission_type == "generic"
    assert plan.budget is None


def test_plan_mission_create_promotes_to_code_when_repo_path_set() -> None:
    plan = plan_mission_create(
        mission_type=None,
        name="x",
        goal="g",
        max_steps=10,
        repo_path="/tmp",
        test_command="pytest",
        lint_command=None,
        build_command=None,
    )
    assert plan.mission_type == "code"
    assert plan.repo_path is not None
    assert plan.test_command == "pytest"
    assert plan.budget is not None and plan.budget.max_steps == 10


def test_plan_mission_create_code_requires_repo_and_test_command() -> None:
    with pytest.raises(ValueError, match="Code missions require"):
        plan_mission_create(
            mission_type="code",
            name="x",
            goal="g",
            max_steps=None,
            repo_path=None,
            test_command=None,
            lint_command=None,
            build_command=None,
        )


def test_plan_mission_create_rejects_non_positive_max_steps() -> None:
    with pytest.raises(ValueError, match="--max-steps"):
        plan_mission_create(
            mission_type=None,
            name="x",
            goal="g",
            max_steps=0,
            repo_path=None,
            test_command=None,
            lint_command=None,
            build_command=None,
        )


def test_plan_mission_run_requires_id() -> None:
    with pytest.raises(ValueError, match="--id"):
        plan_mission_run(mission_id=None, max_iterations=None, step_description=None)


def test_plan_mission_run_defaults_iterations_to_one() -> None:
    plan = plan_mission_run(
        mission_id="mission-abc",
        max_iterations=None,
        step_description=None,
    )
    assert plan.max_iterations == 1


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def test_cli_create_generic_mission_emits_json_payload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _env_isolated(monkeypatch, tmp_path)
    result = CliRunner().invoke(
        app,
        ["mission", "create", "--name", "demo", "--goal", "g", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["name"] == "demo"
    assert payload["goal"] == "g"
    assert payload["status"] == "active"
    # The create handler writes a checkpoint so the artifacts
    # subcommand always has a baseline to list.
    assert Path(payload["checkpointPath"]).is_file()


def test_cli_create_code_mission_persists_metadata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _env_isolated(monkeypatch, tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    result = CliRunner().invoke(
        app,
        [
            "mission",
            "create",
            "--name",
            "code-demo",
            "--goal",
            "g",
            "--type",
            "code",
            "--repo-path",
            str(repo),
            "--test-command",
            "true",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["metadata"]["missionType"] == "code"
    assert payload["metadata"]["testCommand"] == "true"


def test_cli_create_rejects_missing_name(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _env_isolated(monkeypatch, tmp_path)
    result = (
        CliRunner(mix_stderr=False).invoke(app, ["mission", "create", "--goal", "g"])
        if False
        else CliRunner().invoke(app, ["mission", "create", "--goal", "g"])
    )
    # CliRunner combines streams by default; either way the exit
    # code is non-zero on validation failure.
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_cli_list_returns_empty_array_when_no_missions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _env_isolated(monkeypatch, tmp_path)
    result = CliRunner().invoke(app, ["mission", "list", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == []


def test_cli_list_filters_by_status(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _env_isolated(monkeypatch, tmp_path)
    runner = CliRunner()
    runner.invoke(app, ["mission", "create", "--name", "a", "--goal", "g", "--json"])
    runner.invoke(app, ["mission", "create", "--name", "b", "--goal", "g", "--json"])
    result = runner.invoke(app, ["mission", "list", "--status", "active", "--json"])
    assert result.exit_code == 0
    assert len(json.loads(result.stdout)) == 2


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_cli_status_emits_full_payload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _env_isolated(monkeypatch, tmp_path)
    runner = CliRunner()
    created = runner.invoke(app, ["mission", "create", "--name", "x", "--goal", "g", "--json"])
    mid = json.loads(created.stdout)["id"]
    result = runner.invoke(app, ["mission", "status", "--id", mid, "--json"])
    payload = json.loads(result.stdout)
    assert payload["id"] == mid
    assert payload["stepsCount"] == 0
    assert payload["budgetUsage"]["steps_used"] == 0


def test_cli_status_rejects_missing_id(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _env_isolated(monkeypatch, tmp_path)
    result = CliRunner().invoke(app, ["mission", "status"])
    assert result.exit_code == 1


def test_cli_status_rejects_unknown_id(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _env_isolated(monkeypatch, tmp_path)
    result = CliRunner().invoke(app, ["mission", "status", "--id", "mission-nope"])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# artifacts
# ---------------------------------------------------------------------------


def test_cli_artifacts_lists_checkpoint_from_create(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _env_isolated(monkeypatch, tmp_path)
    runner = CliRunner()
    created = runner.invoke(app, ["mission", "create", "--name", "x", "--goal", "g", "--json"])
    mid = json.loads(created.stdout)["id"]
    result = runner.invoke(app, ["mission", "artifacts", "--id", mid, "--json"])
    payload = json.loads(result.stdout)
    assert payload["missionId"] == mid
    assert len(payload["checkpoints"]) >= 1


def test_cli_artifacts_rejects_missing_id(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _env_isolated(monkeypatch, tmp_path)
    result = CliRunner().invoke(app, ["mission", "artifacts"])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def test_cli_run_advances_mission_and_emits_payload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _env_isolated(monkeypatch, tmp_path)
    runner = CliRunner()
    created = runner.invoke(app, ["mission", "create", "--name", "x", "--goal", "g", "--json"])
    mid = json.loads(created.stdout)["id"]
    result = runner.invoke(
        app,
        ["mission", "run", "--id", mid, "--max-iterations", "1", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["id"] == mid
    assert "checkpointPath" in payload


def test_cli_run_rejects_missing_id(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _env_isolated(monkeypatch, tmp_path)
    result = CliRunner().invoke(app, ["mission", "run"])
    assert result.exit_code == 1


def test_cli_run_rejects_non_positive_max_iterations(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _env_isolated(monkeypatch, tmp_path)
    runner = CliRunner()
    created = runner.invoke(app, ["mission", "create", "--name", "x", "--goal", "g", "--json"])
    mid = json.loads(created.stdout)["id"]
    result = runner.invoke(
        app,
        ["mission", "run", "--id", mid, "--max-iterations", "0"],
    )
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# help text
# ---------------------------------------------------------------------------


def test_help_text_lists_slice_4_subcommands() -> None:
    for sub in ("create", "run", "status", "list", "artifacts"):
        assert sub in MISSION_HELP_TEXT


def test_contract_marks_mission_python_yes() -> None:
    """PR #1017 review (P3): registering the public command without
    flipping the contract entry would leave capability/contract
    tooling hiding the new Python surface. The slice-4 subcommands
    are live, so the contract now marks Python as `yes`."""
    contract = (
        json.loads((Path(__file__).resolve().parents[2] / "docs" / "cli-contract.json").read_text(encoding="utf-8"))
        if (Path(__file__).resolve().parents[2] / "docs" / "cli-contract.json").exists()
        else json.loads((Path(__file__).resolve().parents[3] / "docs" / "cli-contract.json").read_text(encoding="utf-8"))
    )
    mission = next(c for c in contract["commands"] if c["id"] == "mission")
    assert mission["runtime_support"]["python"]["status"] == "yes"
    assert mission["runtime_support"]["python"].get("reason") is None
