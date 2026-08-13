"""AC-925: `autoctx skills` — export the host-agnostic SKILL.md files.

Separate from `autoctx hermes export-skill` on purpose. That command emits the
Hermes-specific skill and lives under the Hermes integration group because that
is what it is about. These two are host-agnostic, so putting them under `hermes`
would tell a reader the opposite of the thing this issue asked for.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer

from autocontext.skills.generic import GENERIC_SKILL_RENDERERS

if TYPE_CHECKING:
    from rich.console import Console


def _cli_attr(dependency_module: str, name: str) -> Any:
    return getattr(importlib.import_module(dependency_module), name)


def register_skills_command(
    app: typer.Typer,
    *,
    console: Console,
    dependency_module: str = "autocontext.cli",
) -> None:
    """Register the `skills` command group on ``app``."""

    skills_app = typer.Typer(help="Host-agnostic Autocontext skills")

    @skills_app.command("list")
    def list_skills(
        json_output: Annotated[bool, typer.Option("--json", help="Output structured JSON")] = False,
    ) -> None:
        """List the skills this package can emit."""
        names = sorted(GENERIC_SKILL_RENDERERS)
        if json_output:
            _cli_attr(dependency_module, "_write_json_stdout")({"skills": names})
            return
        for name in names:
            console.print(name)

    @skills_app.command("export")
    def export_skill(
        name: Annotated[
            str,
            typer.Argument(help=f"Which skill to emit: {', '.join(sorted(GENERIC_SKILL_RENDERERS))}"),
        ],
        output: Annotated[
            Path | None,
            typer.Option("--output", help="Write SKILL.md to this path; omit to print it"),
        ] = None,
        force: Annotated[bool, typer.Option("--force", help="Overwrite --output if it exists")] = False,
        json_output: Annotated[bool, typer.Option("--json", help="Output structured JSON")] = False,
    ) -> None:
        """Emit a host-agnostic SKILL.md."""

        renderer = GENERIC_SKILL_RENDERERS.get(name)
        if renderer is None:
            known = ", ".join(sorted(GENERIC_SKILL_RENDERERS))
            message = f"unknown skill {name!r}; known skills: {known}"
            if json_output:
                _cli_attr(dependency_module, "_write_json_stderr")({"error": message})
            else:
                console.print(f"[red]{message}[/red]")
            raise typer.Exit(code=1)

        content = renderer()

        if output is None:
            if json_output:
                _cli_attr(dependency_module, "_write_json_stdout")({"skill": name, "content": content})
            else:
                console.print(content)
            return

        if output.exists() and not force:
            message = f"{output} already exists; pass --force to overwrite"
            if json_output:
                _cli_attr(dependency_module, "_write_json_stderr")({"error": message})
            else:
                console.print(f"[red]{message}[/red]")
            raise typer.Exit(code=1)

        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content, encoding="utf-8")
        if json_output:
            _cli_attr(dependency_module, "_write_json_stdout")({"skill": name, "output": str(output), "bytes": len(content)})
        else:
            console.print(f"wrote {output}")

    app.add_typer(skills_app, name="skills")


__all__ = ["register_skills_command"]
