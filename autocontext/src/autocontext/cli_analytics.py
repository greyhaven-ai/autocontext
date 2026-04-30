from __future__ import annotations

import importlib
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer

from autocontext.analytics.events_to_trace import collect_run_ids, events_to_trace
from autocontext.analytics.run_trace import TraceStore
from autocontext.config.settings import AppSettings
from autocontext.storage import artifact_store_from_settings
from autocontext.storage.run_paths import resolve_run_root

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
    run_ids = [run_id] if run_id else collect_run_ids(source_path)
    if not run_ids:
        failed_result: dict[str, Any] = {"status": "failed", "error": f"No run ids found in {source_path}"}
        if json_output:
            write_json_stdout(failed_result)
        else:
            console.print(f"[red]{failed_result['error']}[/red]")
        raise typer.Exit(code=1)

    artifacts = artifact_store_from_settings(settings)
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
        path = TraceStore(run_root, writer=artifacts.write_json).persist(trace)
        rebuilt.append({
            "run_id": current_run_id,
            "trace_id": trace.trace_id,
            "event_count": len(trace.events),
            "path": str(path),
        })

    result: dict[str, Any] = {"status": "completed", "events_path": str(source_path), "rebuilt": rebuilt}
    if json_output:
        write_json_stdout(result)
        return

    for item in rebuilt:
        console.print(
            f"[green]Rebuilt[/green] {item['trace_id']} " f"({item['event_count']} events) -> {item['path']}"
        )


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

    app.add_typer(analytics_app, name="analytics")
