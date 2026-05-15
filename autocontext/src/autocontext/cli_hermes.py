from __future__ import annotations

import importlib
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer
from rich.table import Table

from autocontext.hermes.curator_ingest import IngestSummary, ingest_curator_reports
from autocontext.hermes.dataset_export import ExportSummary, export_dataset
from autocontext.hermes.inspection import HermesInventory, inspect_hermes_home
from autocontext.hermes.redaction import RedactionPolicy, compile_user_patterns
from autocontext.hermes.references import list_references, render_reference
from autocontext.hermes.session_ingest import SessionIngestSummary, ingest_session_db
from autocontext.hermes.skill import AUTOCONTEXT_HERMES_SKILL_NAME, render_autocontext_skill
from autocontext.hermes.trajectory_ingest import TrajectoryIngestSummary, ingest_trajectory_jsonl

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

    # PR #965 review (P2): preflight every destination before any write so
    # a reference-name collision can't leave SKILL.md half-installed
    # ahead of the failure.
    collisions: list[Path] = []
    if output.exists() and not force:
        collisions.append(output)
    references_dir: Path | None = None
    if with_references:
        references_dir = output.parent / "references"
        if not force:
            for name in list_references():
                candidate = references_dir / f"{name}.md"
                if candidate.exists():
                    collisions.append(candidate)
    if collisions:
        message = "Refusing to overwrite existing files without --force: " + ", ".join(str(p) for p in collisions)
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

    if with_references and references_dir is not None:
        references_dir.mkdir(parents=True, exist_ok=True)
        written: list[dict[str, Any]] = []
        for name in list_references():
            target = references_dir / f"{name}.md"
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


def run_hermes_ingest_curator_command(
    *,
    home: Path | None,
    output: Path,
    since: str | None,
    limit: int | None,
    include_llm_final: bool,
    include_tool_args: bool,
    json_output: bool,
    console: Console,
    write_json_stdout: Any,
) -> None:
    """Ingest Hermes curator reports into a ProductionTrace JSONL file (AC-704)."""

    from autocontext.hermes.inspection import _resolve_hermes_home

    resolved_home = _resolve_hermes_home(home)
    summary: IngestSummary = ingest_curator_reports(
        home=resolved_home,
        output=output,
        since=since,
        limit=limit,
        include_llm_final=include_llm_final,
        include_tool_args=include_tool_args,
    )
    payload = {
        "hermes_home": str(resolved_home),
        "output_path": str(output),
        "runs_read": summary.runs_read,
        "traces_written": summary.traces_written,
        "skipped": summary.skipped,
        "warnings": list(summary.warnings),
    }
    if json_output:
        write_json_stdout(payload)
        return
    console.print(
        f"[green]Ingested[/green] {summary.traces_written}/{summary.runs_read} "
        f"curator runs -> {output} (skipped={summary.skipped})"
    )
    for warning in summary.warnings:
        console.print(f"[yellow]warning:[/yellow] {warning}")


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


def run_hermes_ingest_trajectories_command(
    *,
    input_path: Path,
    output: Path,
    redact: str,
    user_patterns_json: str | None,
    limit: int | None,
    dry_run: bool,
    json_output: bool,
    console: Console,
    write_json_stdout: Any,
    write_json_stderr: Any,
) -> None:
    """Ingest a Hermes trajectory JSONL file with redaction (AC-706 slice 1)."""

    import json as _json

    user_patterns_raw: list[dict[str, str]] | None = None
    if user_patterns_json is not None:
        try:
            parsed = _json.loads(user_patterns_json)
        except _json.JSONDecodeError as err:
            message = f"--user-patterns is not valid JSON: {err.msg}"
            if json_output:
                write_json_stderr(message)
            else:
                console.print(f"[red]{message}[/red]")
            raise typer.Exit(code=1) from err
        if not isinstance(parsed, list):
            message = "--user-patterns must be a JSON array of {{name, pattern}} objects"
            if json_output:
                write_json_stderr(message)
            else:
                console.print(f"[red]{message}[/red]")
            raise typer.Exit(code=1)
        user_patterns_raw = parsed

    try:
        user_patterns = compile_user_patterns(user_patterns_raw)
        policy = RedactionPolicy(mode=redact, user_patterns=user_patterns)
    except ValueError as err:
        if json_output:
            write_json_stderr(str(err))
        else:
            console.print(f"[red]{err}[/red]")
        raise typer.Exit(code=1) from err

    try:
        summary: TrajectoryIngestSummary = ingest_trajectory_jsonl(
            input_path=input_path,
            output_path=output,
            policy=policy,
            limit=limit,
            dry_run=dry_run,
        )
    except (FileNotFoundError, ValueError) as err:
        if json_output:
            write_json_stderr(str(err))
        else:
            console.print(f"[red]{err}[/red]")
        raise typer.Exit(code=1) from err

    if json_output:
        write_json_stdout(summary.to_dict())
        return
    action = "Would write" if dry_run else "Wrote"
    target = str(output) if not dry_run else "(dry-run, no file written)"
    console.print(
        f"[green]{action}[/green] {summary.trajectories_written} redacted trajectories "
        f"({summary.lines_read} lines read, {summary.skipped} skipped) -> {target}"
    )
    if summary.redactions.total:
        console.print(f"[dim]Redactions:[/dim] {summary.redactions.to_dict()}")
    for warning in summary.warnings:
        console.print(f"[yellow]warning:[/yellow] {warning}")


def run_hermes_ingest_sessions_command(
    *,
    home: Path | None,
    output: Path,
    redact: str,
    user_patterns_json: str | None,
    since: str | None,
    limit: int | None,
    dry_run: bool,
    json_output: bool,
    console: Console,
    write_json_stdout: Any,
    write_json_stderr: Any,
) -> None:
    """Ingest Hermes session DB into ProductionTrace JSONL (AC-706 slice 2)."""

    import json as _json

    from autocontext.hermes.inspection import _resolve_hermes_home

    user_patterns_raw: list[dict[str, str]] | None = None
    if user_patterns_json is not None:
        try:
            parsed = _json.loads(user_patterns_json)
        except _json.JSONDecodeError as err:
            message = f"--user-patterns is not valid JSON: {err.msg}"
            if json_output:
                write_json_stderr(message)
            else:
                console.print(f"[red]{message}[/red]")
            raise typer.Exit(code=1) from err
        if not isinstance(parsed, list):
            message = "--user-patterns must be a JSON array of {name, pattern} objects"
            if json_output:
                write_json_stderr(message)
            else:
                console.print(f"[red]{message}[/red]")
            raise typer.Exit(code=1)
        user_patterns_raw = parsed

    try:
        user_patterns = compile_user_patterns(user_patterns_raw)
        policy = RedactionPolicy(mode=redact, user_patterns=user_patterns)
    except ValueError as err:
        if json_output:
            write_json_stderr(str(err))
        else:
            console.print(f"[red]{err}[/red]")
        raise typer.Exit(code=1) from err

    resolved_home = _resolve_hermes_home(home)
    try:
        summary: SessionIngestSummary = ingest_session_db(
            home=resolved_home,
            output=output,
            policy=policy,
            since=since,
            limit=limit,
            dry_run=dry_run,
        )
    except ValueError as err:
        if json_output:
            write_json_stderr(str(err))
        else:
            console.print(f"[red]{err}[/red]")
        raise typer.Exit(code=1) from err

    if json_output:
        write_json_stdout(summary.to_dict())
        return
    action = "Would write" if dry_run else "Wrote"
    target = str(output) if not dry_run else "(dry-run, no file written)"
    console.print(
        f"[green]{action}[/green] {summary.traces_written}/{summary.sessions_read} session traces -> {target}"
    )
    if summary.redactions.total:
        console.print(f"[dim]Redactions:[/dim] {summary.redactions.to_dict()}")
    for warning in summary.warnings:
        console.print(f"[yellow]warning:[/yellow] {warning}")


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

    @hermes_app.command("ingest-curator")
    def ingest_curator(
        home: Annotated[
            Path | None,
            typer.Option("--home", help="Hermes home directory (default: HERMES_HOME or ~/.hermes)"),
        ] = None,
        output: Annotated[
            Path,
            typer.Option("--output", help="Destination JSONL path for ProductionTrace entries"),
        ] = Path("hermes-curator-traces.jsonl"),
        since: Annotated[
            str | None,
            typer.Option("--since", help="ISO-8601 timestamp; skip curator runs strictly before this"),
        ] = None,
        limit: Annotated[
            int | None,
            typer.Option("--limit", help="Maximum number of traces to write"),
        ] = None,
        include_llm_final: Annotated[
            bool,
            typer.Option(
                "--include-llm-final",
                help="Attach the curator's LLM final summary as an assistant message (off by default for privacy)",
            ),
        ] = False,
        include_tool_args: Annotated[
            bool,
            typer.Option(
                "--include-tool-args",
                help="Attach raw tool-call args (off by default to avoid leaking sensitive arguments)",
            ),
        ] = False,
        json_output: Annotated[bool, typer.Option("--json", help="Output structured JSON")] = False,
    ) -> None:
        """Ingest Hermes curator reports into ProductionTrace JSONL (AC-704)."""

        run_hermes_ingest_curator_command(
            home=home,
            output=output,
            since=since,
            limit=limit,
            include_llm_final=include_llm_final,
            include_tool_args=include_tool_args,
            json_output=json_output,
            console=console,
            write_json_stdout=_cli_attr(dependency_module, "_write_json_stdout"),
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

    @hermes_app.command("ingest-trajectories")
    def ingest_trajectories(
        input_path: Annotated[
            Path,
            typer.Option(
                "--input",
                help="Source JSONL file (trajectory_samples.jsonl, failed_trajectories.jsonl, or batch export)",
            ),
        ],
        output: Annotated[
            Path,
            typer.Option("--output", help="Destination JSONL path for redacted trajectories"),
        ] = Path("hermes-trajectories-redacted.jsonl"),
        redact: Annotated[
            str,
            typer.Option(
                "--redact",
                help="Redaction mode: off | standard (default) | strict. 'strict' requires --user-patterns.",
            ),
        ] = "standard",
        user_patterns_json: Annotated[
            str | None,
            typer.Option(
                "--user-patterns",
                help="JSON array of {name, pattern} regex objects for --redact strict",
            ),
        ] = None,
        limit: Annotated[
            int | None,
            typer.Option("--limit", help="Maximum number of trajectories to write"),
        ] = None,
        dry_run: Annotated[
            bool,
            typer.Option(
                "--dry-run",
                help="Count and redact but do not write the output file (AC-706 privacy preview)",
            ),
        ] = False,
        json_output: Annotated[bool, typer.Option("--json", help="Output structured JSON")] = False,
    ) -> None:
        """Ingest a Hermes trajectory JSONL with explicit redaction (AC-706 slice 1)."""

        run_hermes_ingest_trajectories_command(
            input_path=input_path,
            output=output,
            redact=redact,
            user_patterns_json=user_patterns_json,
            limit=limit,
            dry_run=dry_run,
            json_output=json_output,
            console=console,
            write_json_stdout=_cli_attr(dependency_module, "_write_json_stdout"),
            write_json_stderr=_cli_attr(dependency_module, "_write_json_stderr"),
        )

    @hermes_app.command("ingest-sessions")
    def ingest_sessions(
        home: Annotated[
            Path | None,
            typer.Option("--home", help="Hermes home directory (default: HERMES_HOME or ~/.hermes)"),
        ] = None,
        output: Annotated[
            Path,
            typer.Option("--output", help="Destination JSONL path for ProductionTrace entries"),
        ] = Path("hermes-sessions.jsonl"),
        redact: Annotated[
            str,
            typer.Option(
                "--redact",
                help="Redaction mode: off | standard (default) | strict. 'strict' requires --user-patterns.",
            ),
        ] = "standard",
        user_patterns_json: Annotated[
            str | None,
            typer.Option(
                "--user-patterns",
                help="JSON array of {name, pattern} regex objects for --redact strict",
            ),
        ] = None,
        since: Annotated[
            str | None,
            typer.Option("--since", help="ISO-8601 timestamp; skip sessions strictly before this"),
        ] = None,
        limit: Annotated[
            int | None,
            typer.Option("--limit", help="Maximum number of session traces to write"),
        ] = None,
        dry_run: Annotated[
            bool,
            typer.Option(
                "--dry-run",
                help="Count and redact but do not write the output file (AC-706 privacy preview)",
            ),
        ] = False,
        json_output: Annotated[bool, typer.Option("--json", help="Output structured JSON")] = False,
    ) -> None:
        """Ingest Hermes session DB into ProductionTrace JSONL (AC-706 slice 2)."""

        run_hermes_ingest_sessions_command(
            home=home,
            output=output,
            redact=redact,
            user_patterns_json=user_patterns_json,
            since=since,
            limit=limit,
            dry_run=dry_run,
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
