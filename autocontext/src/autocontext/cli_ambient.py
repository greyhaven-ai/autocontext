"""cli for the ambient trainer daemon: init, status, run, once, proposals, approve, reject."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from autocontext.ambient.charter_io import CharterLoadError, load_charter, save_charter
from autocontext.ambient.daemon import AmbientDaemon
from autocontext.ambient.datasets import DatasetStore
from autocontext.ambient.interview import build_charter, run_interview
from autocontext.ambient.proposals import ProposalError, ProposalStore, apply_proposal
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
_DEFAULT_DATASETS = Path("runs/ambient-datasets")
_DEFAULT_PROPOSALS = Path("runs/ambient-proposals.jsonl")


def _daemon(
    charter_path: Path,
    db_path: Path,
    events_path: Path,
    runs_db_path: Path,
    otel_feed_dir: Path,
    datasets_dir: Path,
    proposals_path: Path,
) -> AmbientDaemon:
    charter = load_charter(charter_path)
    emitter = EventStreamEmitter(events_path)
    stages = build_stages(
        charter,
        db_path=db_path,
        emitter=emitter,
        runs_db_path=runs_db_path,
        otel_feed_dir=otel_feed_dir,
        datasets_dir=datasets_dir,
    )
    return AmbientDaemon(
        charter=charter,
        queue=AmbientQueue(db_path),
        emitter=emitter,
        stages=stages,
        proposal_store=ProposalStore(proposals_path),
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
    datasets_dir: Annotated[Path, typer.Option("--datasets-dir")] = _DEFAULT_DATASETS,
    proposals_path: Annotated[Path, typer.Option("--proposals-path")] = _DEFAULT_PROPOSALS,
) -> None:
    try:
        daemon = _daemon(charter_path, db_path, events_path, runs_db, otel_feed_dir, datasets_dir, proposals_path)
    except CharterLoadError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    charter = daemon.charter
    console.print(f"tier={charter.tier} autonomy={charter.autonomy} targets={len(charter.targets)}")
    table = Table("stage", "paused (this process)", "backlog")
    for name, info in daemon.status().items():
        table.add_row(name, str(info["paused"]), str(info["queue_depth"]))
    console.print(table)
    if charter.targets:
        dataset_store = DatasetStore(datasets_dir)
        targets_table = Table("target", "dataset records", "mean score")
        for target in charter.targets:
            manifest = dataset_store.load_manifest(target.name)
            targets_table.add_row(target.name, str(manifest.record_count), f"{manifest.mean_score:.2f}")
        console.print(targets_table)


@ambient_app.command()
def run(
    charter_path: Annotated[Path, typer.Option("--charter-path")] = _DEFAULT_CHARTER,
    db_path: Annotated[Path, typer.Option("--db-path")] = _DEFAULT_DB,
    events_path: Annotated[Path, typer.Option("--events-path")] = _DEFAULT_EVENTS,
    runs_db: Annotated[Path, typer.Option("--runs-db")] = _DEFAULT_RUNS_DB,
    otel_feed_dir: Annotated[Path, typer.Option("--otel-feed-dir")] = _DEFAULT_OTEL_FEED,
    datasets_dir: Annotated[Path, typer.Option("--datasets-dir")] = _DEFAULT_DATASETS,
    proposals_path: Annotated[Path, typer.Option("--proposals-path")] = _DEFAULT_PROPOSALS,
    poll_seconds: Annotated[float, typer.Option("--poll-seconds", min=0.0)] = 30.0,
    max_cycles: Annotated[int | None, typer.Option("--max-cycles", hidden=True)] = None,
) -> None:
    try:
        daemon = _daemon(charter_path, db_path, events_path, runs_db, otel_feed_dir, datasets_dir, proposals_path)
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
    datasets_dir: Annotated[Path, typer.Option("--datasets-dir")] = _DEFAULT_DATASETS,
    proposals_path: Annotated[Path, typer.Option("--proposals-path")] = _DEFAULT_PROPOSALS,
) -> None:
    try:
        daemon = _daemon(charter_path, db_path, events_path, runs_db, otel_feed_dir, datasets_dir, proposals_path)
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


@ambient_app.command()
def proposals(
    charter_path: Annotated[Path, typer.Option("--charter-path")] = _DEFAULT_CHARTER,
    proposals_path: Annotated[Path, typer.Option("--proposals-path")] = _DEFAULT_PROPOSALS,
) -> None:
    store = ProposalStore(proposals_path)
    pending = store.pending()
    if not pending:
        console.print("no pending proposals")
        return
    table = Table("proposal id", "kind", "rationale")
    for proposal in pending:
        table.add_row(proposal.proposal_id, proposal.kind, proposal.rationale)
    console.print(table)


@ambient_app.command()
def approve(
    proposal_id: Annotated[str, typer.Argument()],
    charter_path: Annotated[Path, typer.Option("--charter-path")] = _DEFAULT_CHARTER,
    proposals_path: Annotated[Path, typer.Option("--proposals-path")] = _DEFAULT_PROPOSALS,
) -> None:
    store = ProposalStore(proposals_path)
    match = next((p for p in store.pending() if p.proposal_id == proposal_id), None)
    if match is None:
        console.print(f"[red]no pending proposal with id {proposal_id}[/red]")
        raise typer.Exit(code=1)
    try:
        charter = load_charter(charter_path)
        updated = apply_proposal(charter, match)
    except (CharterLoadError, ProposalError, ValidationError, ValueError) as exc:
        # the proposal stays pending: nothing was applied, so nothing is marked
        console.print(f"[red]could not apply proposal: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    save_charter(updated, charter_path)
    store.mark(proposal_id, "applied")
    console.print(f"applied {proposal_id} to {charter_path}")


@ambient_app.command()
def reject(
    proposal_id: Annotated[str, typer.Argument()],
    charter_path: Annotated[Path, typer.Option("--charter-path")] = _DEFAULT_CHARTER,
    proposals_path: Annotated[Path, typer.Option("--proposals-path")] = _DEFAULT_PROPOSALS,
) -> None:
    store = ProposalStore(proposals_path)
    try:
        store.mark(proposal_id, "rejected")
    except ProposalError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(f"rejected {proposal_id}")
