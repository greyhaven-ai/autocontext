"""``autoctx rescore <run_id> [--generation N] [--json] [--apply] [--by WHO]`` (AC-885 Slice D2a/D2b).

Re-score a stale generation's ORIGINAL competitor artifact under the CURRENT evaluator and report the
old-vs-new score + epoch. Without ``--apply`` this command writes nothing: no ``upsert_generation``, no
registry write, no quarantine clear. With ``--apply`` (Slice D2b) it APPENDS an audit revision to
``generation_score_revisions`` for each matching re-score (``revalidated`` generations whose fresh epoch
equals the active epoch): the fresh score and its epoch, with the generation's current values archived as
the ``previous_*`` columns. It does NOT modify the ``generations`` row, its quarantine marker, or any
derived table (``knowledge_snapshots`` etc.): the live score of record is left untouched, so this cannot
poison training-export or cross-run rankings. Drifted, skipped, and error generations are never recorded.
(Like every other inspect command it opens the existing sqlite store, and it runs the configured evaluator
hooks so the re-score reproduces the production evaluator; it does not create a database for a missing run.)
Re-scoring goes through the scenario task's own ``evaluate_output`` (the production path) inside the
configured hook bus, so the score is faithful; the whole thing is fail-safe via ``revalidate_one``.
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


def _emit_error(message: str, json_output: bool) -> None:
    """Report a not-found error via the CLI's JSON-or-Rich convention.

    Under ``--json`` this writes ``{"error": message}`` to stderr (matching ``_write_json_stderr`` in
    ``cli``, replicated here to avoid importing ``cli`` at module load, which would cycle).
    """
    if json_output:
        import sys

        sys.stderr.write(json.dumps({"error": message}) + "\n")
    else:
        _console.print(f"[red]{message}[/red]")


def _open_store(settings: AppSettings) -> SQLiteStore | None:
    """Open the existing sqlite store read side, or None when the database does not exist.

    Report-only: a missing-run invocation must not CREATE a database. Returning None lets the caller
    surface not-found without materializing an empty store (migrate is applied only to a database that
    already exists, where it is idempotent and writes nothing new).
    """
    from pathlib import Path

    from autocontext.storage.sqlite_store import SQLiteStore

    if not settings.db_path.exists():
        return None
    store = SQLiteStore(settings.db_path)
    store.migrate(Path(__file__).resolve().parents[2] / "migrations")
    return store


def _active_epoch(settings: AppSettings, scenario: str | None) -> str | None:
    """Return the active evaluator epoch id for ``scenario`` (registry read), or None."""
    if not scenario:
        return None
    registry = EvaluatorEpochRegistry(settings.knowledge_root / "_evaluator_epochs")
    record = registry.active_for(scenario)
    return record.epoch_id if record is not None else None


def _build_score_fn(scenario: str, settings: AppSettings) -> ScoreFn | None:
    """Build a re-scoring closure over the scenario task's own ``evaluate_output``.

    Returns None when the scenario is not an agent-task (no reconstructable rubric judge). Custom agent
    tasks are loaded from the CONFIGURED ``settings.knowledge_root`` first (the import-time registry only
    scans a relative ``knowledge/``, so a non-default root would otherwise miss them). The closure
    evaluates inside the configured hook bus so BEFORE_JUDGE / AFTER_JUDGE hooks reproduce the production
    evaluator (a hook can change the effective model, and therefore the stamped epoch, or the response).
    Imports ``_is_agent_task`` lazily from ``cli`` to avoid a circular import.
    """
    from autocontext.cli import _is_agent_task
    from autocontext.extensions import active_hook_bus
    from autocontext.loop.runner_hooks import initialize_hook_bus
    from autocontext.scenarios.custom.registry import load_all_custom_scenarios

    SCENARIO_REGISTRY.update(load_all_custom_scenarios(settings.knowledge_root))
    if not _is_agent_task(scenario):
        return None
    task = SCENARIO_REGISTRY[scenario]()
    state = task.prepare_context(task.initial_state())
    hook_bus, _loaded_extensions = initialize_hook_bus(settings)

    def score_fn(artifact: str) -> tuple[float | None, str | None]:
        with active_hook_bus(hook_bus):
            result = task.evaluate_output(artifact, state)
        return result.score, result.evaluator_epoch

    return score_fn


def _resolve_score_fn(scenario: str | None, settings: AppSettings) -> ScoreFn | None:
    """Build the score fn, converting any construction failure into a per-generation ``error``.

    ``_build_score_fn`` can raise while loading a custom scenario or preparing its context. Rather than
    letting that escape as an uncaught exit, return a closure that raises inside ``revalidate_one`` so the
    failure is reported per generation (fail-safe boundary) instead of crashing the command.
    """
    if not scenario:
        return None
    try:
        return _build_score_fn(scenario, settings)
    except Exception as exc:  # noqa: BLE001 - convert setup failure into a per-generation error status
        message = f"evaluator construction failed: {exc}"

        def failing(artifact: str) -> tuple[float | None, str | None]:
            raise RuntimeError(message)

        return failing


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


def _epoch8(epoch: str | None) -> str:
    return epoch[:8] if epoch else "-"


def _print_table(reports: list[GenerationRevalidation], active_epoch: str | None, applied: list[int]) -> None:
    active8 = active_epoch[:8] if active_epoch else "none"
    table = Table(title=f"Re-score (active epoch {active8}...)")
    table.add_column("Gen", justify="right")
    table.add_column("Status")
    table.add_column("Old score", justify="right")
    table.add_column("New score", justify="right")
    table.add_column("Delta", justify="right")
    table.add_column("Stale")
    table.add_column("Old epoch")
    table.add_column("New epoch")
    table.add_column("Matches active")
    table.add_column("Recorded")
    for rep in reports:
        old = "-" if rep.original_score is None else f"{rep.original_score:.3f}"
        new = "-" if rep.new_score is None else f"{rep.new_score:.3f}"
        delta = "-" if rep.score_delta is None else f"{rep.score_delta:+.3f}"
        matches = "yes" if rep.new_matches_active else ("no" if rep.new_epoch is not None else "-")
        table.add_row(
            str(rep.generation_index),
            rep.status,
            old,
            new,
            delta,
            "yes" if rep.was_stale else "no",
            _epoch8(rep.original_epoch),
            _epoch8(rep.new_epoch),
            matches,
            "yes" if rep.generation_index in applied else "-",
        )
    _console.print(table)
    revalidated = sum(1 for r in reports if r.status == "revalidated")
    if applied:
        written = f"{len(applied)} audit revision(s) recorded (the live score of record was NOT changed)"
    else:
        written = "no score, generation, or lineage data was written"
    _console.print(f"[dim]{revalidated} of {len(reports)} generation(s) re-scored; {written}.[/dim]")
    drifted = [r for r in reports if r.status == "revalidated" and not r.new_matches_active]
    if drifted:
        _console.print(
            f"[yellow]Warning: {len(drifted)} re-scored generation(s) produced an epoch that does NOT "
            f"match the active epoch {active8}...; the current scenario spec has drifted from the active "
            "evaluator, so these fresh scores are not under the active epoch.[/yellow]"
        )


def rescore_command(
    run_id: str = typer.Argument(..., help="Run to re-score"),
    generation: int | None = typer.Option(
        None, "--generation", min=1, help="Re-score a single generation by index (default: all stale rows)"
    ),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Record active-epoch re-scores as audit revisions (does NOT change the live score or quarantine)",
    ),
    by: str = typer.Option("", "--by", help="Reviewer identity recorded on the audit revisions"),
) -> None:
    """Re-score stale generations under the current evaluator and report drift (read-only without --apply)."""
    settings = load_settings()
    store = _open_store(settings)
    if store is None or store.get_run(run_id) is None:
        _emit_error(f"run {run_id!r} not found", json_output)
        raise typer.Exit(code=1)

    run = store.get_run(run_id)
    scenario = run.get("scenario") if run else None
    rows = store.run_status(run_id)
    if generation is not None and not any(r["generation_index"] == generation for r in rows):
        _emit_error(f"run {run_id!r} has no generation {generation}", json_output)
        raise typer.Exit(code=1)

    active_epoch = _active_epoch(settings, scenario)
    targets = _select_rows(rows, generation, active_epoch)
    # Reconstruct the evaluator only when it can actually be used (an active epoch exists and there are
    # targets), and after the generation check, so construction cost and failures stay inside the
    # fail-safe boundary. Setup failures become per-generation ``error`` reports (see _resolve_score_fn).
    score_fn = _resolve_score_fn(scenario, settings) if (active_epoch is not None and targets) else None
    # One competitor output per generation on the solve path. If a generation has more than one
    # (a tournament re-append), keep the last by rowid, matching training-export's _get_competitor_outputs.
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

    # Record matching re-scores as audit revisions only under --apply (default writes nothing). A report is
    # recordable when it is ``revalidated`` AND its fresh epoch equals the active epoch (``new_matches_active``)
    # with a concrete score/epoch. Drifted, skipped, and error generations are never recorded. Recording is
    # append-only: it never touches the live ``generations`` row, its quarantine marker, or derived tables.
    applied: list[int] = []
    if apply:
        for rep in reports:
            if rep.status == "revalidated" and rep.new_matches_active and rep.new_score is not None and rep.new_epoch is not None:
                if store.record_rescore_revision(
                    run_id, rep.generation_index, rep.new_score, rep.new_epoch, created_by=by or None
                ):
                    applied.append(rep.generation_index)

    if json_output:
        payload = {
            "run_id": run_id,
            "scenario": scenario,
            "active_evaluator_epoch": active_epoch,
            "applied": applied,
            "generations": [{**asdict(rep), "applied": rep.generation_index in applied} for rep in reports],
        }
        typer.echo(json.dumps(payload))
        return

    _print_table(reports, active_epoch, applied)
