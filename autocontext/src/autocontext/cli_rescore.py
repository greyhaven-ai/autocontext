"""``autoctx rescore <run_id> [--generation N] [--json]`` (AC-885 Slice D2a).

Re-score a stale generation's ORIGINAL competitor artifact under the CURRENT evaluator and report the
old-vs-new score + epoch. Report-only: this command writes nothing (no upsert_generation, no registry
write, no quarantine clear). Re-scoring goes through the scenario task's own ``evaluate_output`` (the
production path), so the score is faithful; the whole thing is fail-safe via ``revalidate_one``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

import typer
from rich.console import Console
from rich.table import Table

from autocontext.config import load_settings
from autocontext.execution.evaluator_epoch_registry import EvaluatorEpochRegistry
from autocontext.execution.rescore import GenerationRevalidation, revalidate_one
from autocontext.scenarios import SCENARIO_REGISTRY

if TYPE_CHECKING:
    from autocontext.config.settings import AppSettings
    from autocontext.storage.sqlite_store import SQLiteStore

_console = Console()

ScoreFn = Callable[[str], tuple[float | None, str | None]]


def _store(settings: AppSettings) -> SQLiteStore:
    from pathlib import Path

    from autocontext.storage.sqlite_store import SQLiteStore

    store = SQLiteStore(settings.db_path)
    store.migrate(Path(__file__).resolve().parents[2] / "migrations")
    return store


def _active_epoch(settings: AppSettings, scenario: str | None) -> str | None:
    """Return the active evaluator epoch id for ``scenario`` (registry read-only), or None."""
    if not scenario:
        return None
    registry = EvaluatorEpochRegistry(settings.knowledge_root / "_evaluator_epochs")
    record = registry.active_for(scenario)
    return record.epoch_id if record is not None else None


def _build_score_fn(scenario: str, settings: AppSettings) -> ScoreFn | None:
    """Build a re-scoring closure over the scenario task's own ``evaluate_output``.

    Returns None when the scenario is not an agent-task (no reconstructable rubric judge). Imports
    ``_is_agent_task`` lazily from ``cli`` to avoid a circular import (``cli`` imports this module to
    register the command).
    """
    from autocontext.cli import _is_agent_task

    if not _is_agent_task(scenario):
        return None
    task = SCENARIO_REGISTRY[scenario]()
    state = task.prepare_context(task.initial_state())

    def score_fn(artifact: str) -> tuple[float | None, str | None]:
        result = task.evaluate_output(artifact, state)
        return result.score, result.evaluator_epoch

    return score_fn


def _is_stale(row: dict[str, Any], active_epoch: str | None) -> bool:
    epoch = row.get("evaluator_epoch")
    return epoch is not None and active_epoch is not None and epoch != active_epoch


def _select_rows(
    rows: list[dict[str, Any]],
    generation: int | None,
    active_epoch: str | None,
) -> list[dict[str, Any]]:
    """Choose the generations to report on.

    ``--generation N`` targets that single row. Otherwise: with no active epoch, report every row (each
    surfaces ``skipped_no_active_epoch``); with an active epoch, report only the stale rows.
    """
    if generation is not None:
        return [r for r in rows if r["generation_index"] == generation]
    if active_epoch is None:
        return rows
    return [r for r in rows if _is_stale(r, active_epoch)]


def _print_table(reports: list[GenerationRevalidation], active_epoch: str | None) -> None:
    active8 = active_epoch[:8] if active_epoch else "none"
    table = Table(title=f"Re-score (active epoch {active8}...)")
    table.add_column("Gen", justify="right")
    table.add_column("Status")
    table.add_column("Old score", justify="right")
    table.add_column("New score", justify="right")
    table.add_column("Delta", justify="right")
    table.add_column("Stale")
    for rep in reports:
        old = "-" if rep.original_score is None else f"{rep.original_score:.3f}"
        new = "-" if rep.new_score is None else f"{rep.new_score:.3f}"
        delta = "-" if rep.score_delta is None else f"{rep.score_delta:+.3f}"
        table.add_row(str(rep.generation_index), rep.status, old, new, delta, "yes" if rep.was_stale else "no")
    _console.print(table)
    revalidated = sum(1 for r in reports if r.status == "revalidated")
    _console.print(f"[dim]{revalidated} of {len(reports)} generation(s) re-scored; nothing was written.[/dim]")


def rescore_command(
    run_id: str = typer.Argument(..., help="Run to re-score"),
    generation: int | None = typer.Option(
        None, "--generation", min=1, help="Re-score a single generation by index (default: all stale rows)"
    ),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Re-score stale generations under the current evaluator and report drift (read-only)."""
    settings = load_settings()
    store = _store(settings)
    run = store.get_run(run_id)
    if run is None:
        message = f"run {run_id!r} not found"
        if json_output:
            typer.echo(message, err=True)
        else:
            _console.print(f"[red]{message}[/red]")
        raise typer.Exit(code=1)

    scenario = run.get("scenario")
    active_epoch = _active_epoch(settings, scenario)
    score_fn = _build_score_fn(scenario, settings) if scenario else None

    rows = store.run_status(run_id)
    if generation is not None and not any(r["generation_index"] == generation for r in rows):
        message = f"run {run_id!r} has no generation {generation}"
        if json_output:
            typer.echo(message, err=True)
        else:
            _console.print(f"[red]{message}[/red]")
        raise typer.Exit(code=1)

    targets = _select_rows(rows, generation, active_epoch)
    artifacts = {o["generation_index"]: o["content"] for o in store.get_agent_outputs_by_role(run_id, "competitor")}

    reports = [
        revalidate_one(
            r["generation_index"],
            original_score=r.get("best_score"),
            original_epoch=r.get("evaluator_epoch"),
            active_epoch=active_epoch,
            artifact=artifacts.get(r["generation_index"]),
            score_fn=score_fn,
        )
        for r in targets
    ]

    if json_output:
        payload = {
            "run_id": run_id,
            "scenario": scenario,
            "active_evaluator_epoch": active_epoch,
            "generations": [asdict(rep) for rep in reports],
        }
        typer.echo(json.dumps(payload))
        return

    _print_table(reports, active_epoch)
