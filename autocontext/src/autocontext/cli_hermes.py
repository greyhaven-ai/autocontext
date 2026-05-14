from __future__ import annotations

import importlib
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer
from rich.table import Table

from autocontext.hermes.dataset_export import ExportSummary, export_dataset
from autocontext.hermes.inspection import HermesInventory, inspect_hermes_home
from autocontext.hermes.skill import AUTOCONTEXT_HERMES_SKILL_NAME, render_autocontext_skill

if TYPE_CHECKING:
    from rich.console import Console


def _cli_attr(dependency_module: str, name: str) -> Any:
    return getattr(importlib.import_module(dependency_module), name)


def run_hermes_inspect_command(
    *,
    home: Path | None,
    json_output: bool,
    console: Console,
    write_json_stdout: Any,
) -> None:
    """Run the read-only Hermes inventory command."""

    inventory = inspect_hermes_home(home)
    if json_output:
        write_json_stdout(inventory.to_dict())
        return
    _print_inventory(inventory, console=console)


def run_hermes_export_skill_command(
    *,
    output: Path | None,
    force: bool,
    json_output: bool,
    console: Console,
    write_json_stdout: Any,
    write_json_stderr: Any,
) -> None:
    """Emit the bundled Hermes Autocontext skill."""

    skill_markdown = render_autocontext_skill()
    if output is None:
        if json_output:
            write_json_stdout(
                {
                    "skill_name": AUTOCONTEXT_HERMES_SKILL_NAME,
                    "skill_markdown": skill_markdown,
                }
            )
        else:
            console.print(skill_markdown.rstrip())
        return

    if output.exists() and not force:
        message = f"Refusing to overwrite existing file without --force: {output}"
        if json_output:
            write_json_stderr(message)
        else:
            console.print(f"[red]{message}[/red]")
        raise typer.Exit(code=1)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(skill_markdown, encoding="utf-8")
    payload = {
        "skill_name": AUTOCONTEXT_HERMES_SKILL_NAME,
        "output_path": str(output),
        "bytes_written": len(skill_markdown.encode("utf-8")),
    }
    if json_output:
        write_json_stdout(payload)
    else:
        console.print(f"[green]Wrote[/green] {AUTOCONTEXT_HERMES_SKILL_NAME} skill to {output}")


def run_hermes_export_dataset_command(
    *,
    kind: str,
    home: Path | None,
    output: Path,
    since: str | None,
    limit: int | None,
    json_output: bool,
    console: Console,
    write_json_stdout: Any,
) -> None:
    """Export a Hermes curator decision dataset for local training (AC-705)."""

    from autocontext.hermes.inspection import _resolve_hermes_home

    resolved_home = _resolve_hermes_home(home)
    try:
        summary: ExportSummary = export_dataset(
            kind=kind,
            home=resolved_home,
            output=output,
            since=since,
            limit=limit,
        )
    except (NotImplementedError, ValueError) as err:
        if json_output:
            write_json_stdout({"status": "failed", "error": str(err), "kind": kind})
        else:
            console.print(f"[red]{err}[/red]")
        raise typer.Exit(code=1) from err

    payload = {
        "kind": kind,
        "hermes_home": str(resolved_home),
        "output_path": str(output),
        "runs_read": summary.runs_read,
        "examples_written": summary.examples_written,
        "warnings": list(summary.warnings),
    }
    if json_output:
        write_json_stdout(payload)
        return
    console.print(
        f"[green]Exported[/green] {summary.examples_written} {kind} examples from {summary.runs_read} curator run(s) -> {output}"
    )


def register_hermes_command(
    app: typer.Typer,
    *,
    console: Console,
    dependency_module: str = "autocontext.cli",
) -> None:
    hermes_app = typer.Typer(help="Hermes Agent integration helpers")

    @hermes_app.command("inspect")
    def inspect(
        home: Annotated[
            Path | None,
            typer.Option("--home", help="Hermes home directory (default: HERMES_HOME or ~/.hermes)"),
        ] = None,
        json_output: Annotated[bool, typer.Option("--json", help="Output structured JSON")] = False,
    ) -> None:
        """Read Hermes skill usage and Curator reports without mutating Hermes."""

        run_hermes_inspect_command(
            home=home,
            json_output=json_output,
            console=console,
            write_json_stdout=_cli_attr(dependency_module, "_write_json_stdout"),
        )

    @hermes_app.command("export-skill")
    def export_skill(
        output: Annotated[
            Path | None,
            typer.Option("--output", help="Write the Hermes SKILL.md to this path; omit to print it"),
        ] = None,
        force: Annotated[bool, typer.Option("--force", help="Overwrite --output if it already exists")] = False,
        json_output: Annotated[bool, typer.Option("--json", help="Output structured JSON")] = False,
    ) -> None:
        """Emit the first-class Hermes autocontext skill."""

        run_hermes_export_skill_command(
            output=output,
            force=force,
            json_output=json_output,
            console=console,
            write_json_stdout=_cli_attr(dependency_module, "_write_json_stdout"),
            write_json_stderr=_cli_attr(dependency_module, "_write_json_stderr"),
        )

    @hermes_app.command("export-dataset")
    def export_dataset_cmd(
        kind: Annotated[
            str,
            typer.Option(
                "--kind",
                help="Dataset kind: curator-decisions (shipped); other kinds documented but not yet implemented",
            ),
        ] = "curator-decisions",
        home: Annotated[
            Path | None,
            typer.Option("--home", help="Hermes home directory (default: HERMES_HOME or ~/.hermes)"),
        ] = None,
        output: Annotated[
            Path,
            typer.Option("--output", help="Destination JSONL path for training examples"),
        ] = Path("hermes-curator-decisions.jsonl"),
        since: Annotated[
            str | None,
            typer.Option("--since", help="ISO-8601 timestamp; skip curator runs strictly before this"),
        ] = None,
        limit: Annotated[
            int | None,
            typer.Option("--limit", help="Maximum number of examples to write"),
        ] = None,
        json_output: Annotated[bool, typer.Option("--json", help="Output structured JSON")] = False,
    ) -> None:
        """Export Hermes curator decisions as training JSONL (AC-705)."""

        run_hermes_export_dataset_command(
            kind=kind,
            home=home,
            output=output,
            since=since,
            limit=limit,
            json_output=json_output,
            console=console,
            write_json_stdout=_cli_attr(dependency_module, "_write_json_stdout"),
        )

    app.add_typer(hermes_app, name="hermes")


def _print_inventory(inventory: HermesInventory, *, console: Console) -> None:
    console.print(f"[bold]Hermes home:[/bold] {inventory.hermes_home}")
    console.print(
        "[dim]"
        f"skills={inventory.skill_count} "
        f"agent-created={inventory.agent_created_skill_count} "
        f"bundled={inventory.bundled_skill_count} "
        f"hub={inventory.hub_skill_count} "
        f"pinned={inventory.pinned_skill_count} "
        f"archived={inventory.archived_skill_count}"
        "[/dim]"
    )

    table = Table(title="Hermes Skills")
    table.add_column("Name")
    table.add_column("Provenance")
    table.add_column("State")
    table.add_column("Pinned")
    table.add_column("Activity")
    table.add_column("Last Activity")
    for skill in inventory.skills:
        table.add_row(
            skill.name,
            skill.provenance,
            skill.state,
            "yes" if skill.pinned else "no",
            str(skill.activity_count),
            skill.last_activity_at or "",
        )
    console.print(table)

    latest = inventory.curator.latest
    if latest is None:
        console.print("[dim]No Hermes Curator reports found.[/dim]")
        return
    console.print(
        "[bold]Latest curator run:[/bold] "
        f"{latest.started_at or latest.path.parent.name} "
        f"consolidated={latest.counts.get('consolidated_this_run', latest.consolidated_count)} "
        f"pruned={latest.counts.get('pruned_this_run', latest.pruned_count)} "
        f"archived={latest.counts.get('archived_this_run', latest.archived_count)}"
    )
