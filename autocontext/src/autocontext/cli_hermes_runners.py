"""Runner functions backing the `autoctx hermes` typer subcommands.

Split out of ``cli_hermes.py`` (PR #973 review P1) so the
subcommand-registration module stays under the 800-LOC guard. Each
``run_hermes_*_command`` here is the pure-Python body the matching
typer subcommand calls; CLI presentation concerns (Console,
write_json_stdout, write_json_stderr) are passed in by the caller
so this module has no rich/typer rendering imports of its own.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer
from rich.table import Table

from autocontext.hermes.advisor import (
    AdvisorMetrics,
    evaluate,
    load_curator_examples,
    train_baseline,
)
from autocontext.hermes.curator_ingest import IngestSummary, ingest_curator_reports
from autocontext.hermes.dataset_export import ExportSummary, export_dataset
from autocontext.hermes.inspection import HermesInventory, inspect_hermes_home
from autocontext.hermes.recommendations import Recommendation, recommend
from autocontext.hermes.redaction import RedactionPolicy, compile_user_patterns
from autocontext.hermes.references import list_references, render_reference
from autocontext.hermes.session_ingest import SessionIngestSummary, ingest_session_db
from autocontext.hermes.skill import AUTOCONTEXT_HERMES_SKILL_NAME, render_autocontext_skill
from autocontext.hermes.trajectory_ingest import TrajectoryIngestSummary, ingest_trajectory_jsonl

if TYPE_CHECKING:
    from rich.console import Console


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


def _same_file(a: Path, b: Path) -> bool:
    """Return True when ``a`` and ``b`` point at the same file (resolved)."""
    if a.exists() and b.exists():
        try:
            return a.samefile(b)
        except OSError:
            return False
    return a.resolve() == b.resolve()


def run_hermes_train_advisor_command(
    *,
    data: Path,
    baseline: bool,
    output: Path | None,
    json_output: bool,
    console: Console,
    write_json_stdout: Any,
    write_json_stderr: Any,
) -> None:
    """Train and evaluate a Hermes curator advisor (AC-708 slice 1)."""

    import json as _json

    # PR #972 review (P2): refuse to overwrite the source dataset.
    if output is not None and _same_file(data, output):
        message = (
            f"output {output!s} resolves to the same file as --data {data!s}; "
            "refusing to overwrite the source dataset"
        )
        if json_output:
            write_json_stderr(message)
        else:
            console.print(f"[red]{message}[/red]")
        raise typer.Exit(code=1)

    examples = load_curator_examples(data)
    if not examples:
        message = f"no labeled examples loaded from {data}"
        if json_output:
            write_json_stderr(message)
        else:
            console.print(f"[red]{message}[/red]")
        raise typer.Exit(code=1)

    if not baseline:
        message = "only --baseline is supported in this slice; trained backends arrive in AC-708 slice 2"
        if json_output:
            write_json_stderr(message)
        else:
            console.print(f"[red]{message}[/red]")
        raise typer.Exit(code=1)

    advisor = train_baseline(examples)
    # AC-708 slice 1 evaluates the baseline against the training set as
    # a sanity check; held-out splits arrive with the trained backends.
    metrics: AdvisorMetrics = evaluate(advisor, examples)
    payload = {
        "advisor_kind": "baseline",
        "majority_label": advisor.majority_label,
        "label_counts": dict(advisor.label_counts),
        "metrics": metrics.to_dict(),
    }
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(_json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if json_output:
        write_json_stdout(payload)
        return
    console.print(
        f"[green]Trained baseline[/green] majority={advisor.majority_label!r} on {metrics.example_count} examples; "
        f"accuracy={metrics.accuracy:.3f}"
    )
    if metrics.insufficient_data:
        console.print(
            f"[yellow]warning:[/yellow] only {metrics.example_count} examples; per-label metrics may not be meaningful"
        )
    for label, m in metrics.per_label.items():
        console.print(f"  {label}: precision={m.precision:.3f} recall={m.recall:.3f} support={m.support}")


def run_hermes_recommend_command(
    *,
    home: Path | None,
    baseline_from: Path,
    output: Path,
    include_protected: bool,
    json_output: bool,
    console: Console,
    write_json_stdout: Any,
    write_json_stderr: Any,
) -> None:
    """Emit read-only recommendations from a trained advisor (AC-709)."""

    import json as _json

    from autocontext.hermes.inspection import _resolve_hermes_home, inspect_hermes_home

    # Same-file guard matches the AC-706 / AC-708 ingest posture: never
    # overwrite the training input with the recommendation output.
    if _same_file(baseline_from, output):
        message = (
            f"output {output!s} resolves to the same file as --baseline-from "
            f"{baseline_from!s}; refusing to overwrite the training input"
        )
        if json_output:
            write_json_stderr(message)
        else:
            console.print(f"[red]{message}[/red]")
        raise typer.Exit(code=1)

    # PR #973 review (P2): the recommendation surface promises it never
    # writes to the Hermes home. Reject `--output` paths that resolve
    # inside the home so a typo cannot break the read-only invariant.
    resolved_home_for_guard = _resolve_hermes_home(home)
    if _is_inside(output, resolved_home_for_guard):
        message = (
            f"output {output!s} resolves inside Hermes home {resolved_home_for_guard!s}; "
            "refusing to write under ~/.hermes (AC-709 read-only invariant)"
        )
        if json_output:
            write_json_stderr(message)
        else:
            console.print(f"[red]{message}[/red]")
        raise typer.Exit(code=1)

    examples = load_curator_examples(baseline_from)
    if not examples:
        message = f"no labeled examples loaded from {baseline_from}; cannot train baseline advisor"
        if json_output:
            write_json_stderr(message)
        else:
            console.print(f"[red]{message}[/red]")
        raise typer.Exit(code=1)

    advisor = train_baseline(examples)
    resolved_home = _resolve_hermes_home(home)
    inventory = inspect_hermes_home(resolved_home)
    recs: list[Recommendation] = recommend(
        inventory=inventory,
        advisor=advisor,
        include_protected=include_protected,
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as fh:
        for rec in recs:
            fh.write(_json.dumps(rec.to_dict(), separators=(",", ":")) + "\n")

    actionable = sum(1 for r in recs if r.status == "actionable")
    protected = sum(1 for r in recs if r.status == "protected")
    payload = {
        "home": str(resolved_home),
        "output_path": str(output),
        "advisor_kind": "baseline",
        "majority_label": advisor.majority_label,
        "recommendation_count": len(recs),
        "actionable_count": actionable,
        "protected_count": protected,
    }
    if json_output:
        write_json_stdout(payload)
        return
    console.print(
        f"[green]Wrote[/green] {len(recs)} recommendation(s) ({actionable} actionable, {protected} protected) -> {output}"
    )
    if not recs:
        console.print("[dim]No unprotected skills in inventory; no recommendations emitted.[/dim]")




def _is_inside(path: Path, parent: Path) -> bool:
    """Return True when ``path`` resolves inside ``parent`` (or equals it)."""
    try:
        resolved_path = path.resolve()
        resolved_parent = parent.resolve()
    except OSError:
        return False
    if resolved_path == resolved_parent:
        return True
    try:
        return resolved_path.is_relative_to(resolved_parent)
    except AttributeError:
        # Python <3.9 fallback (not relevant here, but cheap to keep).
        return str(resolved_path).startswith(str(resolved_parent) + "/")


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


__all__ = [
    "run_hermes_export_dataset_command",
    "run_hermes_export_skill_command",
    "run_hermes_ingest_curator_command",
    "run_hermes_ingest_sessions_command",
    "run_hermes_ingest_trajectories_command",
    "run_hermes_inspect_command",
    "run_hermes_recommend_command",
    "run_hermes_train_advisor_command",
]
