"""Tests for ab-test CLI command."""
from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

import pytest
from typer.main import get_command
from typer.testing import CliRunner

from autocontext.cli import app

runner = CliRunner()


def test_ab_test_command_exists() -> None:
    result = runner.invoke(app, ["ab-test", "--help"])
    assert result.exit_code == 0
    assert "baseline" in result.output.lower() or "A/B" in result.output


def test_ab_test_help_shows_options() -> None:
    result = runner.invoke(app, ["ab-test", "--help"])
    assert result.exit_code == 0
    command = get_command(app).get_command(None, "ab-test")
    assert command is not None
    option_names = {param.name for param in command.params}
    option_flags = {flag for param in command.params for flag in getattr(param, "opts", [])}
    assert {"scenario", "runs", "gens", "seed"} <= option_names
    assert {"--scenario", "--runs", "--gens", "--seed"} <= option_flags


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in [key for key in os.environ if key.startswith("AUTOCONTEXT_")]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("AUTOCONTEXT_AGENT_PROVIDER", "deterministic")
    monkeypatch.setenv("AUTOCONTEXT_MATCHES_PER_GENERATION", "1")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_ab_test_reports_a_failed_arm_as_a_clean_error(workspace: Path) -> None:
    """A failing arm escaped the command as a raw exception and printed a traceback."""
    result = runner.invoke(app, ["ab-test", "--scenario", "nope", "--runs", "1", "--gens", "1"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit), result.exception
    assert "Error: Unknown scenario 'nope'" in result.output


@pytest.mark.slow
def test_ab_test_prints_the_experiment_id_its_runs_are_recorded_under(workspace: Path) -> None:
    """A fresh workspace crashed on the unmigrated database, and fixed run ids gave no
    way to tell one A/B test's runs from another's."""
    result = runner.invoke(app, ["ab-test", "--scenario", "othello", "--runs", "1", "--gens", "1"])

    assert result.exit_code == 0, result.output
    match = re.search(r"Experiment: (\w+)", result.output)
    assert match is not None, result.output
    conn = sqlite3.connect(workspace / "runs" / "autocontext.sqlite3")
    try:
        run_ids = {row[0] for row in conn.execute("SELECT run_id FROM runs")}
    finally:
        conn.close()
    assert run_ids == {f"ab_{match.group(1)}_baseline_0", f"ab_{match.group(1)}_treatment_0"}
