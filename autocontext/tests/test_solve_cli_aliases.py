"""Tests for AC-737: ``solve`` CLI flag aliases (``--task-file``, ``--generations``).

The bug: operators copy older docs/patterns and pass ``--task-file
foo.txt`` or ``--generations 30`` to ``autoctx solve``. The current CLI
errors confusingly (or, on some Typer versions, silently routes to
defaults) because those flags are not registered. AC-737 introduces:

- ``--task-file <path>`` reads the file as the task description (mutually
  exclusive with ``--description``).
- ``--generations`` registered as an alias for ``--gens``.
"""

from __future__ import annotations

from pathlib import Path

import typer.main as typer_main


def _solve_command_params() -> dict[str, list[str]]:
    """Map each registered solve param to its accepted flag names."""
    from autocontext.cli import app

    click_app = typer_main.get_command(app)
    solve_cmd = click_app.commands["solve"]
    out: dict[str, list[str]] = {}
    for param in solve_cmd.params:
        out[param.name] = list(getattr(param, "opts", []))
    return out


class TestFlagSurface:
    def test_task_file_flag_is_registered(self):
        params = _solve_command_params()
        flat = {flag for opts in params.values() for flag in opts}
        assert "--task-file" in flat, f"--task-file should be registered; got {sorted(flat)}"

    def test_generations_alias_is_registered(self):
        params = _solve_command_params()
        flat = {flag for opts in params.values() for flag in opts}
        assert "--generations" in flat, f"--generations should be registered as alias for --gens; got {sorted(flat)}"

    def test_gens_short_form_still_works(self):
        # We must not regress the existing --gens flag while adding the alias.
        params = _solve_command_params()
        flat = {flag for opts in params.values() for flag in opts}
        assert "--gens" in flat


# -- End-to-end CLI invocation --


class TestTaskFileEndToEnd:
    def test_task_file_routes_to_description(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        """``solve --task-file <path>`` must populate description from the file."""
        captured: dict[str, str] = {}

        def _stub_run(**kwargs):  # noqa: ANN001
            captured.update(kwargs)
            # Don't actually run a solve — we only care about routing.
            raise SystemExit(0)

        monkeypatch.setattr(
            "autocontext.cli_solve.run_solve_command",
            _stub_run,
        )

        # Write a marker into the task file.
        marker = "TASK-FILE-CONTENT-MARKER-9999"
        f = tmp_path / "task.txt"
        f.write_text(f"Prove the lemma. {marker}", encoding="utf-8")

        from typer.testing import CliRunner

        from autocontext.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["solve", "--task-file", str(f)])
        # SystemExit(0) is expected because of the stub.
        assert result.exit_code == 0
        assert "description" in captured
        assert marker in captured["description"]

    def test_task_file_and_description_together_errors(
        self,
        tmp_path: Path,
    ):
        from typer.testing import CliRunner

        from autocontext.cli import app

        f = tmp_path / "task.txt"
        f.write_text("from-file", encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "solve",
                "--description",
                "from-text",
                "--task-file",
                str(f),
            ],
        )
        assert result.exit_code != 0
        # Error mentions the conflict so the operator can fix it.
        out = (result.stdout or "") + (result.stderr or "")
        assert "exclusive" in out.lower() or "both" in out.lower()

    def test_neither_description_nor_task_file_errors(self):
        from typer.testing import CliRunner

        from autocontext.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["solve"])
        assert result.exit_code != 0
        # Error names at least one of the options so the user knows what to do.
        # Strip ANSI + collapse whitespace so terminal-width line-wrapping
        # in CI doesn't split flag names like "--task-" / "file".
        import re

        raw = (result.stdout or "") + (result.stderr or "")
        out = re.sub(r"\x1b\[[0-9;]*m", "", raw)
        out = re.sub(r"\s+", " ", out)
        assert "--description" in out or "--task-file" in out

    def test_missing_task_file_errors(self, tmp_path: Path):
        from typer.testing import CliRunner

        from autocontext.cli import app

        missing = tmp_path / "does-not-exist.txt"
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["solve", "--task-file", str(missing)],
        )
        assert result.exit_code != 0
        out = (result.stdout or "") + (result.stderr or "")
        assert "not found" in out.lower() or str(missing) in out
