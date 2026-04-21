from __future__ import annotations

import dataclasses
import importlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer
from rich.table import Table

from autocontext.cli_runtime_overrides import (
    apply_solve_runtime_overrides,
    format_runtime_provider_error,
    solve_primary_runtime_provider,
)
from autocontext.config.settings import AppSettings
from autocontext.providers.base import ProviderError
from autocontext.util.json_io import write_json

if TYPE_CHECKING:
    from rich.console import Console


def _validate_family_override(family_name: str | None) -> None:
    """Validate the --family flag value. Raises typer.Exit(1) on unknown.

    Empty string and None both mean "not provided" → no raise.
    """
    from autocontext.scenarios.families import list_families

    if not family_name:
        return
    known = {f.name for f in list_families()}
    if family_name not in known:
        typer.echo(
            f"unknown --family {family_name!r}. Valid: {sorted(known)}",
            err=True,
        )
        raise typer.Exit(code=1)


@dataclass(slots=True)
class SolveRunSummary:
    """Result summary for solve-on-demand via the CLI."""

    job_id: str
    status: str
    description: str
    scenario_name: str | None
    generations: int
    progress: int
    output_path: str | None
    llm_classifier_fallback_used: bool
    result: dict[str, Any] | None


def _cli_attr(dependency_module: str, name: str) -> Any:
    return getattr(importlib.import_module(dependency_module), name)


def run_solve_command(
    *,
    description: str,
    gens: int,
    timeout: float | None,
    generation_time_budget: int | None,
    output: str,
    json_output: bool,
    console: Console,
    load_settings_fn: Callable[[], AppSettings],
    write_json_stdout: Callable[[object], None],
    write_json_stderr: Callable[[str], None],
    family_override: str | None = None,
) -> None:
    """Create a scenario on demand, run it, and export the solved package."""
    from autocontext.knowledge.solver import SolveManager

    settings = apply_solve_runtime_overrides(
        load_settings_fn(),
        timeout=timeout,
        generation_time_budget_seconds=generation_time_budget,
    )
    manager = SolveManager(settings)

    try:
        job = manager.solve_sync(
            description=description,
            generations=gens,
            family_override=family_override or None,
        )
    except KeyboardInterrupt:
        if json_output:
            write_json_stderr("solve interrupted")
        else:
            console.print("[red]Solve interrupted[/red]")
        raise typer.Exit(code=1) from None
    except Exception as exc:
        if json_output:
            write_json_stderr(str(exc))
        else:
            console.print(f"[red]Solve failed:[/red] {exc}")
        raise typer.Exit(code=1) from None

    if job.status != "completed" or job.result is None:
        message = job.error or "solve did not complete successfully"
        if "timeout" in message.lower():
            message = format_runtime_provider_error(
                ProviderError(message),
                provider_name=solve_primary_runtime_provider(settings),
                settings=settings,
            )
        if json_output:
            write_json_stderr(message)
        else:
            console.print(f"[red]Solve failed:[/red] {message}")
        raise typer.Exit(code=1)

    output_path: str | None = None
    if output:
        output_file = Path(output)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        write_json(output_file, job.result.to_dict())
        output_path = str(output_file)

    summary = SolveRunSummary(
        job_id=job.job_id,
        status=job.status,
        description=job.description,
        scenario_name=job.scenario_name,
        generations=job.generations,
        progress=job.progress,
        output_path=output_path,
        llm_classifier_fallback_used=job.llm_classifier_fallback_used,
        result=job.result.to_dict(),
    )

    if json_output:
        write_json_stdout(dataclasses.asdict(summary))
        return

    table = Table(title="Solve Result")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("Job ID", job.job_id)
    table.add_row("Status", job.status)
    table.add_row("Scenario", job.scenario_name or "unknown")
    table.add_row("Generations", str(job.generations))
    table.add_row("Progress", str(job.progress))
    table.add_row(
        "LLM Fallback",
        "yes" if job.llm_classifier_fallback_used else "no",
    )
    if output_path is not None:
        table.add_row("Output", output_path)
    console.print(table)


def register_solve_command(
    app: typer.Typer,
    *,
    console: Console,
    dependency_module: str = "autocontext.cli",
) -> None:
    @app.command()
    def solve(
        description: str = typer.Option(..., "--description", help="Natural-language scenario/problem description"),
        gens: int = typer.Option(5, "--gens", min=1, max=50, help="Generations to run for the solve"),
        timeout: float | None = typer.Option(
            None,
            "--timeout",
            min=1.0,
            help="Provider timeout override in seconds for solve creation/execution runtimes",
        ),
        generation_time_budget: int | None = typer.Option(
            None,
            "--generation-time-budget",
            min=0,
            help="Soft per-generation time budget in seconds for solve runs (0 = unlimited)",
        ),
        output: str = typer.Option("", "--output", help="Optional JSON file path for the solved package"),
        json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
        family: str = typer.Option(
            "",
            "--family",
            help="Force a specific scenario family, bypassing the keyword classifier",
        ),
    ) -> None:
        _validate_family_override(family)
        run_solve_command(
            description=description,
            gens=gens,
            timeout=timeout,
            generation_time_budget=generation_time_budget,
            output=output,
            json_output=json_output,
            console=console,
            load_settings_fn=_cli_attr(dependency_module, "load_settings"),
            write_json_stdout=_cli_attr(dependency_module, "_write_json_stdout"),
            write_json_stderr=_cli_attr(dependency_module, "_write_json_stderr"),
            family_override=family or None,
        )
