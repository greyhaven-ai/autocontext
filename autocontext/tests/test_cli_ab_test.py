"""Tests for ab-test CLI command."""
from __future__ import annotations

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
