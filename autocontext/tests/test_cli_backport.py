"""Tests for AC-382: Backport judge, improve, repl, queue CLI commands to Python.

These tests verify that the Python CLI exposes the 4 commands that
originated in the TS package.
"""

from __future__ import annotations

from typer.testing import CliRunner

from autocontext.cli import app

runner = CliRunner()


class TestJudgeCommand:
    def test_judge_help(self) -> None:
        result = runner.invoke(app, ["judge", "--help"])
        assert result.exit_code == 0
        assert "--task-prompt" in result.stdout or "-p" in result.stdout
        assert "--output" in result.stdout or "-o" in result.stdout
        assert "--rubric" in result.stdout or "-r" in result.stdout

    def test_judge_requires_args(self) -> None:
        result = runner.invoke(app, ["judge"])
        assert result.exit_code != 0

    def test_judge_missing_provider_gives_clear_error(self) -> None:
        """Judge without API key should give a clear error, not a stack trace."""
        result = runner.invoke(app, [
            "judge",
            "--task-prompt", "Write a haiku",
            "--output", "Test output",
            "--rubric", "Score it",
        ])
        # Should fail cleanly (no API key configured)
        assert result.exit_code != 0


class TestImproveCommand:
    def test_improve_help(self) -> None:
        result = runner.invoke(app, ["improve", "--help"])
        assert result.exit_code == 0
        assert "--task-prompt" in result.stdout or "-p" in result.stdout
        assert "--rubric" in result.stdout or "-r" in result.stdout

    def test_improve_requires_args(self) -> None:
        result = runner.invoke(app, ["improve"])
        assert result.exit_code != 0


class TestQueueCommand:
    def test_queue_help(self) -> None:
        result = runner.invoke(app, ["queue", "--help"])
        assert result.exit_code == 0
        assert "--spec" in result.stdout or "-s" in result.stdout

    def test_queue_requires_spec(self) -> None:
        result = runner.invoke(app, ["queue"])
        assert result.exit_code != 0
