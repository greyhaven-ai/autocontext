"""Concise default help and expanded command catalog for the Python CLI."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

PAVED_ROAD_SUMMARIES = {
    "solve": "Solve a required plain-language goal; defaults to 5 iterations. Next: show.",
    "run": "Run a scenario; pass it explicitly in Python or configure it for npm. Defaults to 1 iteration. Next: status.",
    "status": "Show a required run's current snapshot; defaults to text output. Queue: autoctx queue status. Next: watch.",
    "watch": "Follow a required run; defaults to polling every 2 seconds. Next: show.",
    "show": "Inspect a required run; defaults to its latest generation. Next: export.",
    "export": "Export a required run or scenario; defaults to JSON on stdout. Next: import-package.",
}

_PAVED_ROAD_COMMANDS = tuple(PAVED_ROAD_SUMMARIES)
_SETUP_COMMANDS = ("commands", "capabilities")
_MANAGE_COMMANDS = ("list", "replay", "resume", "scenario", "queue")
_SERVICE_COMMANDS = ("serve", "tui")
_COMPATIBILITY_ALIASES = {"mcp-serve": "serve mcp", "new-scenario": "scenario create"}
_DEFAULT_HELP_ORDER = (*_PAVED_ROAD_COMMANDS, *_SETUP_COMMANDS, *_MANAGE_COMMANDS, *_SERVICE_COMMANDS)


def _registered_command_name(info: Any) -> str:
    return str(info.name or (info.callback.__name__ if info.callback else ""))


def _command_category(name: str) -> str:
    if name in _PAVED_ROAD_COMMANDS:
        return "Paved road"
    if name in _SETUP_COMMANDS:
        return "Setup"
    if name in _MANAGE_COMMANDS:
        return "Manage"
    if name in _SERVICE_COMMANDS:
        return "Services"
    if name in _COMPATIBILITY_ALIASES:
        return "Compatibility"
    return "Advanced"


def _command_summary(info: Any, name: str) -> str:
    if name in PAVED_ROAD_SUMMARIES:
        return PAVED_ROAD_SUMMARIES[name]
    if name in _COMPATIBILITY_ALIASES:
        return f"Deprecated alias for `autoctx {_COMPATIBILITY_ALIASES[name]}`."
    callback = getattr(info, "callback", None)
    summary = getattr(info, "help", None) or (getattr(callback, "__doc__", None) if callback else None)
    return str(summary or "Advanced command.").strip().splitlines()[0]


def _command_catalog_entries(app: typer.Typer, *, include_all: bool) -> list[tuple[str, str, str]]:
    entries: list[tuple[str, str, str]] = []
    for command_info in app.registered_commands:
        name = _registered_command_name(command_info)
        category = _command_category(name)
        if include_all or name in _DEFAULT_HELP_ORDER:
            entries.append((category, name, _command_summary(command_info, name)))
    for group_info in app.registered_groups:
        name = str(group_info.name or "")
        category = _command_category(name)
        if include_all or name in _DEFAULT_HELP_ORDER:
            entries.append((category, name, str(group_info.help or "Command group.")))
    category_order = {"Paved road": 0, "Setup": 1, "Manage": 2, "Services": 3, "Advanced": 4, "Compatibility": 5}
    command_order = {name: index for index, name in enumerate(_DEFAULT_HELP_ORDER)}
    return sorted(entries, key=lambda entry: (category_order[entry[0]], command_order.get(entry[1], 999), entry[1]))


def configure_help_surface(
    app: typer.Typer,
    console: Console,
    *,
    version: str,
    write_json_stdout: Callable[[object], None],
) -> None:
    """Register the catalog command and arrange the default help surface."""

    @app.callback()
    def main_callback(
        ctx: typer.Context,
        show_version: bool = typer.Option(False, "--version", is_eager=True, help="Show package version and exit."),
        json_output: bool = typer.Option(False, "--json", hidden=True),
    ) -> None:
        """Render version metadata or the concise default help surface."""
        if show_version:
            if json_output:
                write_json_stdout({"package": "autocontext", "version": version, "runtime": "python"})
            else:
                typer.echo(version)
            raise typer.Exit()
        if ctx.invoked_subcommand is None:
            typer.echo(ctx.get_help())

    @app.command("commands", rich_help_panel="Setup")
    def commands_catalog(
        all_commands: bool = typer.Option(False, "--all", help="Include advanced commands and compatibility aliases."),
    ) -> None:
        """List the concise command set or the full command catalog."""
        entries = _command_catalog_entries(app, include_all=all_commands)
        table = Table(title="All commands" if all_commands else "Recommended commands")
        table.add_column("Category")
        table.add_column("Command")
        table.add_column("Outcome")
        for category, name, summary in entries:
            table.add_row(category, name, summary)
        console.print(table)
        if not all_commands:
            console.print("[dim]Run `autoctx commands --all` for advanced commands and compatibility aliases.[/dim]")

    command_order = {name: index for index, name in enumerate(_DEFAULT_HELP_ORDER)}
    for command_info in app.registered_commands:
        name = _registered_command_name(command_info)
        command_info.rich_help_panel = _command_category(name)
        command_info.hidden = name not in _DEFAULT_HELP_ORDER
        if name in PAVED_ROAD_SUMMARIES:
            command_info.help = PAVED_ROAD_SUMMARIES[name]
        if name in _COMPATIBILITY_ALIASES:
            command_info.deprecated = True
    app.registered_commands.sort(
        key=lambda info: (command_order.get(_registered_command_name(info), 999), _registered_command_name(info))
    )

    for group_info in app.registered_groups:
        name = str(group_info.name or "")
        group_info.rich_help_panel = _command_category(name)
        group_info.hidden = name not in _DEFAULT_HELP_ORDER
    app.registered_groups.sort(
        key=lambda group_info: (command_order.get(str(group_info.name or ""), 999), str(group_info.name or ""))
    )
