"""cli for the ambient trainer daemon: init, status, run, once."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from autocontext.ambient.charter_io import CharterLoadError, load_charter, save_charter
from autocontext.ambient.daemon import AmbientDaemon
from autocontext.ambient.interview import build_charter, run_interview
from autocontext.ambient.queue import AmbientQueue
from autocontext.ambient.stage_factory import build_stages
from autocontext.harness.core.events import EventStreamEmitter

ambient_app = typer.Typer(help="ambient trainer: resident daemon that learns from traces")
console = Console()

_DEFAULT_CHARTER = Path("ambient-charter.yaml")
_DEFAULT_DB = Path("runs/ambient.sqlite3")
_DEFAULT_EVENTS = Path("runs/ambient-events.ndjson")
_DEFAULT_RUNS_DB = Path("runs/autocontext.sqlite3")
_DEFAULT_OTEL_FEED = Path("runs/ambient-otel-feed")


def _daemon(
    charter_path: Path,
    db_path: Path,
    events_path: Path,
    runs_db_path: Path,
    otel_feed_dir: Path,
) -> AmbientDaemon:
    charter = load_charter(charter_path)
    emitter = EventStreamEmitter(events_path)
    stages = build_stages(
        charter,
        db_path=db_path,
        emitter=emitter,
        runs_db_path=runs_db_path,
        otel_feed_dir=otel_feed_dir,
    )
    return AmbientDaemon(
        charter=charter,
        queue=AmbientQueue(db_path),
        emitter=emitter,
        stages=stages,
    )


@ambient_app.command()
def init(
    charter_path: Annotated[Path, typer.Option("--charter-path")] = _DEFAULT_CHARTER,
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    if charter_path.exists() and not force:
        console.print(f"[red]charter already exists at {charter_path}; use --force to overwrite[/red]")
        raise typer.Exit(code=1)
    try:
        answers = run_interview(lambda question, default: typer.prompt(question, default=default))
        charter = build_charter(answers)
    except (ValueError, ValidationError) as exc:
        console.print(f"[red]invalid interview input: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    save_charter(charter, charter_path)
    console.print(f"charter written to {charter_path}")


@ambient_app.command()
def status(
    charter_path: Annotated[Path, typer.Option("--charter-path")] = _DEFAULT_CHARTER,
    db_path: Annotated[Path, typer.Option("--db-path")] = _DEFAULT_DB,
    events_path: Annotated[Path, typer.Option("--events-path")] = _DEFAULT_EVENTS,
    runs_db: Annotated[Path, typer.Option("--runs-db")] = _DEFAULT_RUNS_DB,
    otel_feed_dir: Annotated[Path, typer.Option("--otel-feed-dir")] = _DEFAULT_OTEL_FEED,
) -> None:
    try:
        daemon = _daemon(charter_path, db_path, events_path, runs_db, otel_feed_dir)
    except CharterLoadError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    charter = daemon.charter
    console.print(f"tier={charter.tier} autonomy={charter.autonomy} targets={len(charter.targets)}")
    table = Table("stage", "paused (this process)", "backlog")
    for name, info in daemon.status().items():
        table.add_row(name, str(info["paused"]), str(info["queue_depth"]))
    console.print(table)


@ambient_app.command()
def run(
    charter_path: Annotated[Path, typer.Option("--charter-path")] = _DEFAULT_CHARTER,
    db_path: Annotated[Path, typer.Option("--db-path")] = _DEFAULT_DB,
    events_path: Annotated[Path, typer.Option("--events-path")] = _DEFAULT_EVENTS,
    runs_db: Annotated[Path, typer.Option("--runs-db")] = _DEFAULT_RUNS_DB,
    otel_feed_dir: Annotated[Path, typer.Option("--otel-feed-dir")] = _DEFAULT_OTEL_FEED,
    poll_seconds: Annotated[float, typer.Option("--poll-seconds", min=0.0)] = 30.0,
    max_cycles: Annotated[int | None, typer.Option("--max-cycles", hidden=True)] = None,
) -> None:
    try:
        daemon = _daemon(charter_path, db_path, events_path, runs_db, otel_feed_dir)
    except CharterLoadError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    try:
        daemon.run_forever(poll_seconds=poll_seconds, max_cycles=max_cycles)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc


@ambient_app.command()
def once(
    stage: Annotated[str, typer.Argument()],
    charter_path: Annotated[Path, typer.Option("--charter-path")] = _DEFAULT_CHARTER,
    db_path: Annotated[Path, typer.Option("--db-path")] = _DEFAULT_DB,
    events_path: Annotated[Path, typer.Option("--events-path")] = _DEFAULT_EVENTS,
    runs_db: Annotated[Path, typer.Option("--runs-db")] = _DEFAULT_RUNS_DB,
    otel_feed_dir: Annotated[Path, typer.Option("--otel-feed-dir")] = _DEFAULT_OTEL_FEED,
) -> None:
    try:
        daemon = _daemon(charter_path, db_path, events_path, runs_db, otel_feed_dir)
    except CharterLoadError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    try:
        result = daemon.run_stage_once(stage)
    except KeyError as exc:
        console.print(f"[red]unknown stage: {stage}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(f"processed={result.processed} errors={result.errors}")
    if result.errors > 0:
        raise typer.Exit(code=1)
