"""Tests for AC-851: `cli_error_boundary`, the shared KeyboardInterrupt/Exception
handler extracted from the triplicated try/except blocks in `run()` (both
branches) and `resume()`.

Exercises the context manager directly (decoupled from the full Typer app)
to pin down: pass-through on success, JSON vs Rich output per exception type,
the action-verb-dependent interrupted message, exit code, and exception
chaining (`from None` / `from exc`).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import typer

from autocontext.cli import cli_error_boundary


class TestCliErrorBoundarySuccess:
    def test_no_exception_runs_body_and_raises_nothing(self) -> None:
        ran = False
        with cli_error_boundary(json_output=False, action="run"):
            ran = True
        assert ran is True


class TestCliErrorBoundaryKeyboardInterrupt:
    def test_json_mode_writes_action_interrupted_to_stderr(self) -> None:
        with patch("autocontext.cli._write_json_stderr") as mock_write:
            with pytest.raises(typer.Exit) as exc_info:
                with cli_error_boundary(json_output=True, action="run"):
                    raise KeyboardInterrupt
        mock_write.assert_called_once_with("run interrupted")
        assert exc_info.value.exit_code == 1
        assert exc_info.value.__cause__ is None

    def test_json_mode_uses_action_verb_for_resume(self) -> None:
        with patch("autocontext.cli._write_json_stderr") as mock_write:
            with pytest.raises(typer.Exit):
                with cli_error_boundary(json_output=True, action="resume"):
                    raise KeyboardInterrupt
        mock_write.assert_called_once_with("resume interrupted")

    def test_non_json_mode_prints_capitalized_action_interrupted(self) -> None:
        with patch("autocontext.cli.console") as mock_console:
            with pytest.raises(typer.Exit) as exc_info:
                with cli_error_boundary(json_output=False, action="run"):
                    raise KeyboardInterrupt
        mock_console.print.assert_called_once_with("[yellow]Run interrupted.[/yellow]")
        assert exc_info.value.exit_code == 1

    def test_non_json_mode_resume_message(self) -> None:
        with patch("autocontext.cli.console") as mock_console:
            with pytest.raises(typer.Exit):
                with cli_error_boundary(json_output=False, action="resume"):
                    raise KeyboardInterrupt
        mock_console.print.assert_called_once_with("[yellow]Resume interrupted.[/yellow]")


class TestCliErrorBoundaryException:
    def test_json_mode_writes_exception_message_to_stderr(self) -> None:
        with patch("autocontext.cli._write_json_stderr") as mock_write:
            with pytest.raises(typer.Exit) as exc_info:
                with cli_error_boundary(json_output=True, action="run"):
                    raise RuntimeError("boom")
        mock_write.assert_called_once_with("boom")
        assert exc_info.value.exit_code == 1
        assert isinstance(exc_info.value.__cause__, RuntimeError)
        assert str(exc_info.value.__cause__) == "boom"

    def test_non_json_mode_prints_generic_error(self) -> None:
        with patch("autocontext.cli.console") as mock_console:
            with pytest.raises(typer.Exit) as exc_info:
                with cli_error_boundary(json_output=False, action="run"):
                    raise RuntimeError("boom")
        mock_console.print.assert_called_once_with("[red]Error: boom[/red]")
        assert exc_info.value.exit_code == 1

    def test_typer_exit_raised_in_body_is_caught_like_any_exception(self) -> None:
        """typer.Exit is itself a RuntimeError subclass, so (matching the
        pre-refactor try/except blocks) it is caught by the generic
        `except Exception` branch and re-wrapped as exit code 1, rather than
        propagating its original exit code. None of the current call sites
        (`_run_agent_task`, `_runner().run()`) raise typer.Exit internally,
        so this is a documented edge case rather than a real code path."""
        with pytest.raises(typer.Exit) as exc_info:
            with cli_error_boundary(json_output=False, action="run"):
                raise typer.Exit(code=2)
        assert exc_info.value.exit_code == 1
