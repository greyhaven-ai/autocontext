"""End-to-end test for AC-738: ``solve --family <typo>`` fails with suggestion.

The unit tests in test_cli_family_name.py cover the value object. This
module pins the wiring: when the operator types ``--family agent-task``
the CLI exits non-zero and prints a "did you mean ``agent_task``?"
message to stderr.
"""

from __future__ import annotations

import re

from typer.testing import CliRunner


def _strip_ansi_and_collapse(s: str) -> str:
    """Normalize CLI output for substring assertions.

    Rich/Click renders ANSI color codes and wraps lines based on terminal
    width — both interfere with naive substring matching. Strip the
    escape codes and collapse runs of whitespace.
    """
    s = re.sub(r"\x1b\[[0-9;]*m", "", s)
    return re.sub(r"\s+", " ", s)


class TestSolveFamilyTypo:
    def test_dash_typo_is_rejected_with_suggestion(self):
        from autocontext.cli import app

        runner = CliRunner()
        result = runner.invoke(
            app,
            ["solve", "--description", "x", "--family", "agent-task"],
        )
        assert result.exit_code != 0
        out = _strip_ansi_and_collapse(
            (result.stdout or "") + (result.stderr or ""),
        )
        assert "agent_task" in out
        assert "did you mean" in out.lower() or "?" in out

    def test_completely_unknown_family_lists_valid_set(self):
        from autocontext.cli import app

        runner = CliRunner()
        result = runner.invoke(
            app,
            ["solve", "--description", "x", "--family", "zzz_bogus_family"],
        )
        assert result.exit_code != 0
        out = _strip_ansi_and_collapse(
            (result.stdout or "") + (result.stderr or ""),
        )
        # Falls back to listing valid families.
        assert "agent_task" in out

    def test_correct_family_does_not_error_at_validation(self):
        # We only care here that --family agent_task does NOT trip the
        # typo-suggestion error path. The actual solve will fail later
        # because the test environment has no LLM provider, but it
        # should NOT fail with the "unknown --family" message.
        from autocontext.cli import app

        runner = CliRunner()
        result = runner.invoke(
            app,
            ["solve", "--description", "x", "--family", "agent_task"],
        )
        out = _strip_ansi_and_collapse(
            (result.stdout or "") + (result.stderr or ""),
        )
        assert "unknown --family" not in out.lower()
