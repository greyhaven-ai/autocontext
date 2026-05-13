from __future__ import annotations

import importlib
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer

from autocontext.analytics.events_to_trace import collect_run_ids, events_to_trace
from autocontext.analytics.rubric_drift import DriftStore, RubricDriftMonitor
from autocontext.analytics.run_trace import TraceStore
from autocontext.config.settings import AppSettings
from autocontext.knowledge.context_selection_report import build_context_selection_report
from autocontext.storage import artifact_store_from_settings
from autocontext.storage.context_selection_store import load_context_selection_decisions
from autocontext.storage.run_paths import resolve_run_root
from autocontext.storage.sqlite_store import SQLiteStore

if TYPE_CHECKING:
    from rich.console import Console


def _cli_attr(dependency_module: str, name: str) -> Any:
    return getattr(importlib.import_module(dependency_module), name)


def run_rebuild_traces_command(
    *,
    run_id: str,
    events_path: Path | None,
    json_output: bool,
    console: Console,
    load_settings_fn: Callable[[], AppSettings],
    write_json_stdout: Callable[[object], None],
) -> None:
    """Rebuild RunTrace artifacts from an events.ndjson stream."""
    settings = load_settings_fn()
    source_path = events_path or settings.event_stream_path
    available_run_ids = collect_run_ids(source_path)
    if run_id and run_id not in available_run_ids:
        missing_result: dict[str, Any] = {
            "status": "failed",
            "error": f"No events found for run id {run_id!r} in {source_path}",
            "run_id": run_id,
        }
        if json_output:
            write_json_stdout(missing_result)
        else:
            console.print(f"[red]{missing_result['error']}[/red]")
        raise typer.Exit(code=1)

    run_ids = [run_id] if run_id else available_run_ids
    if not run_ids:
        empty_result: dict[str, Any] = {"status": "failed", "error": f"No run ids found in {source_path}"}
        if json_output:
            write_json_stdout(empty_result)
        else:
            console.print(f"[red]{empty_result['error']}[/red]")
        raise typer.Exit(code=1)

    artifacts = artifact_store_from_settings(settings)
    analytics_store = TraceStore(settings.knowledge_root / "analytics", writer=artifacts.write_json)
    rebuilt: list[dict[str, Any]] = []
    for current_run_id in run_ids:
        try:
            run_root = resolve_run_root(settings.runs_root, current_run_id)
        except ValueError as exc:
            failed_result = {"status": "failed", "error": str(exc), "run_id": current_run_id}
            if json_output:
                write_json_stdout(failed_result)
            else:
                console.print(f"[red]{failed_result['error']}[/red]")
            raise typer.Exit(code=1) from exc

        trace = events_to_trace(source_path, current_run_id)
        analytics_path = analytics_store.persist(trace)
        path = TraceStore(run_root, writer=artifacts.write_json).persist(trace)
        rebuilt.append({
            "run_id": current_run_id,
            "trace_id": trace.trace_id,
            "event_count": len(trace.events),
            "path": str(path),
            "analytics_path": str(analytics_path),
        })

    result: dict[str, Any] = {"status": "completed", "events_path": str(source_path), "rebuilt": rebuilt}
    if json_output:
        write_json_stdout(result)
        return

    for item in rebuilt:
        console.print(
            f"[green]Rebuilt[/green] {item['trace_id']} " f"({item['event_count']} events) -> {item['path']}"
        )


def run_drift_command(
    *,
    run_id: str,
    json_output: bool,
    console: Console,
    load_settings_fn: Callable[[], AppSettings],
    write_json_stdout: Callable[[object], None],
) -> None:
    """Analyze dimension-level rubric drift for a completed run."""
    settings = load_settings_fn()
    sqlite = SQLiteStore(settings.db_path)
    sqlite.migrate(Path(__file__).resolve().parents[2] / "migrations")
    trajectory = sqlite.get_generation_trajectory(run_id)
    if not trajectory:
        result: dict[str, Any] = {"status": "failed", "error": f"No completed generations found for {run_id!r}"}
        if json_output:
            write_json_stdout(result)
        else:
            console.print(f"[red]{result['error']}[/red]")
        raise typer.Exit(code=1)

    monitor = RubricDriftMonitor()
    snapshot = monitor.compute_dimension_snapshot(run_id, trajectory)
    warnings = monitor.detect_dimension_drift(snapshot)
    store = DriftStore(settings.knowledge_root / "analytics")
    warning_paths = [str(store.persist_warning(warning)) for warning in warnings]
    result = {
        "status": "completed",
        "run_id": run_id,
        "snapshot": snapshot.to_dict(),
        "warnings": [warning.to_dict() for warning in warnings],
        "warning_paths": warning_paths,
    }
    if json_output:
        write_json_stdout(result)
        return

    console.print(
        f"[green]Analyzed[/green] {snapshot.dimension_count} dimension(s) "
        f"across {snapshot.generation_count} generation(s)."
    )
    if not warnings:
        console.print("[dim]No dimension-level drift warnings.[/dim]")
        return
    for warning in warnings:
        console.print(f"[yellow]{warning.warning_type}[/yellow] {warning.description}")


def run_context_selection_command(
    *,
    run_id: str,
    json_output: bool,
    console: Console,
    load_settings_fn: Callable[[], AppSettings],
    write_json_stdout: Callable[[object], None],
) -> None:
    """Summarize persisted context-selection artifacts for one run."""
    settings = load_settings_fn()
    try:
        decisions = load_context_selection_decisions(settings.runs_root, run_id)
    except ValueError as exc:
        result = {"status": "failed", "error": str(exc), "run_id": run_id}
        if json_output:
            write_json_stdout(result)
        else:
            console.print(f"[red]{result['error']}[/red]")
        raise typer.Exit(code=1) from exc

    if not decisions:
        result = {
            "status": "failed",
            "error": f"No context selection artifacts found for {run_id!r}",
            "run_id": run_id,
        }
        if json_output:
            write_json_stdout(result)
        else:
            console.print(f"[red]{result['error']}[/red]")
        raise typer.Exit(code=1)

    report = build_context_selection_report(decisions)
    payload = report.to_dict()
    if json_output:
        write_json_stdout(payload)
        return
    console.print(report.to_markdown())


def register_analytics_command(
    app: typer.Typer,
    *,
    console: Console,
    dependency_module: str = "autocontext.cli",
) -> None:
    analytics_app = typer.Typer(help="analytics utilities")

    @analytics_app.command("rebuild-traces")
    def rebuild_traces(
        run_id: Annotated[
            str,
            typer.Option("--run-id", help="Run id to rebuild (default: all run ids in events stream)"),
        ] = "",
        events_path: Annotated[Path | None, typer.Option("--events", help="Path to events.ndjson")] = None,
        json_output: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
    ) -> None:
        run_rebuild_traces_command(
            run_id=run_id,
            events_path=events_path,
            json_output=json_output,
            console=console,
            load_settings_fn=_cli_attr(dependency_module, "load_settings"),
            write_json_stdout=_cli_attr(dependency_module, "_write_json_stdout"),
        )

    @analytics_app.command("drift")
    def drift(
        run_id: Annotated[str, typer.Option("--run-id", help="Run id to analyze")],
        json_output: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
    ) -> None:
        run_drift_command(
            run_id=run_id,
            json_output=json_output,
            console=console,
            load_settings_fn=_cli_attr(dependency_module, "load_settings"),
            write_json_stdout=_cli_attr(dependency_module, "_write_json_stdout"),
        )

    @analytics_app.command("context-selection")
    def context_selection(
        run_id: Annotated[str, typer.Option("--run-id", help="Run id to inspect")],
        json_output: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
    ) -> None:
        run_context_selection_command(
            run_id=run_id,
            json_output=json_output,
            console=console,
            load_settings_fn=_cli_attr(dependency_module, "load_settings"),
            write_json_stdout=_cli_attr(dependency_module, "_write_json_stdout"),
        )

    app.add_typer(analytics_app, name="analytics")
