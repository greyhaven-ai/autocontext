"""Tests for ab-test CLI command."""
from __future__ import annotations

from typer.testing import CliRunner

from mts.cli import app

runner = CliRunner()


def test_ab_test_command_exists() -> None:
    result = runner.invoke(app, ["ab-test", "--help"])
    assert result.exit_code == 0
    assert "baseline" in result.output.lower() or "A/B" in result.output


def test_ab_test_help_shows_options() -> None:
    result = runner.invoke(app, ["ab-test", "--help"])
    assert result.exit_code == 0
    assert "--scenario" in result.output
    assert "--runs" in result.output
    assert "--gens" in result.output
    assert "--seed" in result.output
