"""``autoctx resume``: continue a run with the scenario and target stored on its row."""

from __future__ import annotations

import dataclasses
import importlib
from typing import TYPE_CHECKING, Any, NoReturn

import typer

from autocontext.loop.generation_runner import NON_LOOP_EXECUTOR_MODES

if TYPE_CHECKING:
    from rich.console import Console


def _cli_attr(dependency_module: str, name: str) -> Any:
    return getattr(importlib.import_module(dependency_module), name)


def register_resume_command(
    app: typer.Typer,
    *,
    console: Console,
    dependency_module: str = "autocontext.cli",
) -> None:
    """Mount ``resume`` on ``app``; dependencies resolve lazily so tests can patch ``autocontext.cli``."""

    @app.command()
    def resume(
        run_id: str = typer.Argument(...),
        scenario: str | None = typer.Option(
            None, "--scenario", "-s", help="Scenario the run must belong to. Defaults to the run's own scenario."
        ),
        iterations: int | None = typer.Option(
            None, "--iterations", min=1, help="Generation target. Defaults to the run's stored target; a larger value extends it."
        ),
        gens: int | None = typer.Option(None, "--gens", "-g", min=1, help="Deprecated alias for --iterations."),
        json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
    ) -> None:
        """Resume an existing run idempotently."""
        error_boundary = _cli_attr(dependency_module, "cli_error_boundary")

        def fail(message: str, code: int) -> NoReturn:
            if json_output:
                _cli_attr(dependency_module, "_write_json_stderr")(message)
            else:
                typer.secho(f"Error: {message}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=code)

        # Settings and DB errors keep the execution-error shape (exit 1, JSON on stderr);
        # the usage checks below stay outside the boundary so they keep exit 2.
        with error_boundary(json_output, action="resume"):
            settings = _cli_attr(dependency_module, "load_settings")()
            run = _cli_attr(dependency_module, "_sqlite_from_settings")(settings).get_run(run_id)
        if run is None:
            fail(f"run {run_id!r} not found", 1)
        stored_scenario = str(run["scenario"])
        stored_target = int(run["target_generations"])
        requested = (scenario or "").strip()
        if requested and requested != stored_scenario:
            fail(f"run {run_id!r} belongs to scenario {stored_scenario!r}, not {requested!r}", 2)
        if run.get("executor_mode") in NON_LOOP_EXECUTOR_MODES:
            fail(f"run {run_id!r} was not created by the generation loop and cannot be resumed", 2)
        target = iterations if iterations is not None else gens if gens is not None else stored_target
        if target < stored_target:
            fail(f"run {run_id!r} targets {stored_target} generations; --iterations cannot lower it to {target}", 2)
        minimum = min(int(run.get("minimum_generations") or 1), target)

        with error_boundary(json_output, action="resume"):
            runner = _cli_attr(dependency_module, "_runner")()
            summary = runner.run(scenario_name=stored_scenario, generations=target, run_id=run_id, minimum_generations=minimum)
        if json_output:
            _cli_attr(dependency_module, "_write_json_stdout")(dataclasses.asdict(summary))
        else:
            console.print(f"Resumed {summary.run_id} with {summary.generations_executed} executed generation(s).")
