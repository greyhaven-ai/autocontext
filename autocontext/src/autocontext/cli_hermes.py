from __future__ import annotations

import importlib
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer
from rich.table import Table

from autocontext.hermes.inspection import HermesInventory, inspect_hermes_home
from autocontext.hermes.references import list_references, render_reference
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
    with_references: bool,
    json_output: bool,
    console: Console,
    write_json_stdout: Any,
    write_json_stderr: Any,
) -> None:
    """Emit the bundled Hermes autocontext skill."""

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
    payload: dict[str, Any] = {
        "skill_name": AUTOCONTEXT_HERMES_SKILL_NAME,
        "output_path": str(output),
        "bytes_written": len(skill_markdown.encode("utf-8")),
    }

    if with_references:
        references_dir = output.parent / "references"
        references_dir.mkdir(parents=True, exist_ok=True)
        written: list[dict[str, Any]] = []
        for name in list_references():
            target = references_dir / f"{name}.md"
            if target.exists() and not force:
                message = f"Refusing to overwrite existing reference without --force: {target}"
                if json_output:
                    write_json_stderr(message)
                else:
                    console.print(f"[red]{message}[/red]")
                raise typer.Exit(code=1)
            body = render_reference(name)
            target.write_text(body, encoding="utf-8")
            written.append({"name": name, "path": str(target), "bytes_written": len(body.encode("utf-8"))})
        payload["references"] = written
        payload["references_dir"] = str(references_dir)

    if json_output:
        write_json_stdout(payload)
    else:
        console.print(f"[green]Wrote[/green] {AUTOCONTEXT_HERMES_SKILL_NAME} skill to {output}")
        if with_references:
            console.print(f"[green]Wrote[/green] {len(payload['references'])} references to {payload['references_dir']}")


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
        force: Annotated[bool, typer.Option("--force", help="Overwrite --output and any existing references")] = False,
        with_references: Annotated[
            bool,
            typer.Option(
                "--with-references",
                help="Also write progressive-disclosure references next to SKILL.md (AC-702)",
            ),
        ] = False,
        json_output: Annotated[bool, typer.Option("--json", help="Output structured JSON")] = False,
    ) -> None:
        """Emit the first-class Hermes autocontext skill."""

        run_hermes_export_skill_command(
            output=output,
            force=force,
            with_references=with_references,
            json_output=json_output,
            console=console,
            write_json_stdout=_cli_attr(dependency_module, "_write_json_stdout"),
            write_json_stderr=_cli_attr(dependency_module, "_write_json_stderr"),
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
