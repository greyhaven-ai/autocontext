from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from autocontext import __version__
from autocontext.cli import app

runner = CliRunner()
HELP_LAYOUT = json.loads(
    (Path(__file__).resolve().parents[2] / "docs" / "cli-fixtures" / "help-layout-v1.json").read_text(encoding="utf-8")
)


def _plain(value: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", value)


def _squash(value: str) -> str:
    return re.sub(r"\s+", " ", _plain(value).replace("│", " ")).strip()


def test_default_help_is_paved_road_first_and_concise() -> None:
    result = runner.invoke(app, ["--help"], terminal_width=240)
    output = _plain(result.stdout)

    assert result.exit_code == 0
    offsets = [output.index(f"│ {name}") for name in HELP_LAYOUT["paved_road"]]
    assert offsets == sorted(offsets)
    assert all(category in output for category in HELP_LAYOUT["categories"])
    assert "benchmark" not in output
    assert "new-scenario" not in output
    assert "mcp-serve" not in output
    assert "commands --all" in output
    for summary in HELP_LAYOUT["summaries"].values():
        assert summary in _squash(output)


def test_paved_road_command_help_uses_shared_outcome_summaries() -> None:
    for command, summary in HELP_LAYOUT["summaries"].items():
        result = runner.invoke(app, [command, "--help"], terminal_width=240)

        assert result.exit_code == 0, result.output
        assert summary in _squash(result.stdout)


def test_full_catalog_keeps_advanced_commands_and_aliases_discoverable() -> None:
    result = runner.invoke(app, ["commands", "--all"])
    output = _plain(result.stdout)

    assert result.exit_code == 0
    assert "Advanced" in output
    assert "benchmark" in output
    assert "Compatibility" in output
    assert "new-scenario" in output
    assert "scenario create" in output
    assert "mcp-serve" in output
    normalized = re.sub(r"[^a-zA-Z0-9-]+", " ", output)
    assert "serve mcp" in normalized


def test_bare_cli_uses_the_same_concise_next_step() -> None:
    result = runner.invoke(app, [])

    assert result.exit_code == 0
    assert "Paved road" in _plain(result.stdout)
    assert 'autoctx solve "your goal"' in _plain(result.stdout)


def test_version_reports_runtime_identity_in_json_mode() -> None:
    plain = runner.invoke(app, ["--version"])
    structured = runner.invoke(app, ["--version", "--json"])

    assert plain.exit_code == 0
    assert plain.stdout.strip() == __version__
    assert structured.exit_code == 0
    assert json.loads(structured.stdout) == {
        "package": "autocontext",
        "version": __version__,
        "runtime": "python",
    }


def test_run_without_a_scenario_is_a_usage_error() -> None:
    result = runner.invoke(app, ["run", "--json"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert json.loads(result.stderr) == {
        "error": "no scenario configured; pass <scenario> or --scenario <name>."
    }


def test_run_prefers_canonical_iterations_over_gens() -> None:
    from autocontext.loop.generation_runner import RunSummary

    run = MagicMock(
        return_value=RunSummary(
            run_id="run-precedence",
            scenario="grid_ctf",
            generations_executed=4,
            best_score=0.8,
            current_elo=1200.0,
        )
    )
    runner_mock = MagicMock()
    runner_mock.run = run

    with patch("autocontext.cli._runner", return_value=runner_mock):
        result = runner.invoke(
            app,
            ["run", "grid_ctf", "--gens", "2", "--iterations", "4", "--skip-preflight", "--json"],
        )

    assert result.exit_code == 0, result.output
    run.assert_called_once_with(scenario_name="grid_ctf", generations=4, run_id=None)
