"""AC-697 slice 7: `autoctx show` + `autoctx watch` commands.

Extracted from ``cli.py`` (PR #1002 review P1) so the parent module
stays under the 1600-line guard. Both commands compose the existing
``store.get_run`` and ``store.run_status`` read surfaces.

``show <run-id> [--best] [--generation N] [--json]``
    Renders a finished run's metadata + selected generations. ``--best``
    filters to the single top-scoring generation; ``--generation N``
    filters to a specific index.

``watch <run-id> [--interval N] [--json]``
    Polls run status on an interval and emits one transition-aware
    line / JSONL row per change. Exits when EITHER the latest
    generation is in a terminal status OR the run row itself is in
    a terminal status with no generations (PR #1002 review P2: a
    failed run with ``run_status=[]`` previously looped forever).
"""

from __future__ import annotations

import importlib
import json
import sys
import time
from typing import TYPE_CHECKING, Any

import typer
from rich.table import Table

from autocontext.execution.epoch_lineage import annotate_status_rows
from autocontext.execution.evaluator_epoch_registry import EvaluatorEpochRegistry

if TYPE_CHECKING:
    from rich.console import Console

    from autocontext.config.settings import AppSettings

_TERMINAL_STATUSES = frozenset({"completed", "failed", "succeeded", "errored"})

# AC-885 Slice D1: map the four-state lineage classification to a compact rich "Lineage" cell.
_LINEAGE_LABELS = {"current": "ok", "stale": "stale", "unknown": "legacy", "no_active_epoch": "-"}


def annotate_run_status_rows(
    settings: AppSettings,
    scenario: str | None,
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str | None]:
    """Annotate run_status rows with epoch lineage (AC-885 Slice D1).

    Shared by ``show`` and ``status`` so the registry root is constructed in one place. Builds the
    per-scenario epoch registry under ``settings.knowledge_root / "_evaluator_epochs"`` (same root as
    ``cli_epoch``) and delegates to ``annotate_status_rows``. Read-only.
    """
    registry = EvaluatorEpochRegistry(settings.knowledge_root / "_evaluator_epochs")
    return annotate_status_rows(rows, scenario, registry)


def _lineage_cell(row: dict[str, Any]) -> str:
    """Render the ``Lineage`` column value, marking quarantined rows."""
    label = _LINEAGE_LABELS.get(row.get("evaluator_epoch_status", ""), "-")
    if row.get("quarantined"):
        label = f"{label}+quarantined"
    return label


def _stale_warning_line(rows: list[dict[str, Any]], active_epoch_id: str | None) -> str | None:
    """Return a single warning line if any row is stale or quarantined, else None."""
    flagged = [r for r in rows if r.get("evaluator_epoch_status") == "stale" or r.get("quarantined")]
    if not flagged:
        return None
    active8 = active_epoch_id[:8] if active_epoch_id else "none"
    return (
        f"[yellow]Warning: {len(flagged)} generation(s) scored under a stale evaluator epoch; active epoch {active8}...[/yellow]"
    )


def _cli_attr(dependency_module: str, name: str) -> Any:
    return getattr(importlib.import_module(dependency_module), name)


def register_run_inspect_commands(
    app: typer.Typer,
    *,
    console: Console,
    dependency_module: str = "autocontext.cli",
) -> None:
    """Mount `show` and `watch` on ``app``.

    Both commands resolve `load_settings` / `_sqlite_from_settings` /
    `_write_json_stdout` / `_write_json_stderr` from the
    ``dependency_module`` so tests can substitute store stubs by
    monkey-patching the parent module.
    """

    @app.command()
    def show(
        run_id: str = typer.Argument(...),
        best: bool = typer.Option(False, "--best", help="Show only the best-scoring generation"),
        generation: int | None = typer.Option(None, "--generation", min=1, help="Show a specific generation by index"),
        json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
    ) -> None:
        """Render a finished run's artifacts (run metadata + selected generations)."""
        load_settings = _cli_attr(dependency_module, "load_settings")
        sqlite_from_settings = _cli_attr(dependency_module, "_sqlite_from_settings")
        write_json_stdout = _cli_attr(dependency_module, "_write_json_stdout")
        write_json_stderr = _cli_attr(dependency_module, "_write_json_stderr")

        settings = load_settings()
        store = sqlite_from_settings(settings)
        run = store.get_run(run_id)
        if run is None:
            message = f"run {run_id!r} not found"
            if json_output:
                write_json_stderr(message)
            else:
                console.print(f"[red]{message}[/red]")
            raise typer.Exit(code=1)

        rows = store.run_status(run_id)
        if best and rows:
            rows = [max(rows, key=lambda r: r["best_score"])]
        elif generation is not None:
            rows = [r for r in rows if r["generation_index"] == generation]
            if not rows:
                message = f"run {run_id!r} has no generation {generation}"
                if json_output:
                    write_json_stderr(message)
                else:
                    console.print(f"[red]{message}[/red]")
                raise typer.Exit(code=1)

        rows, active_epoch_id = annotate_run_status_rows(settings, run.get("scenario"), rows)

        if json_output:
            payload: dict[str, Any] = {
                "run_id": run_id,
                "scenario": run.get("scenario"),
                "status": run.get("status"),
                "active_evaluator_epoch": active_epoch_id,
                "generations": [
                    {
                        "generation": r["generation_index"],
                        "mean_score": r["mean_score"],
                        "best_score": r["best_score"],
                        "elo": r["elo"],
                        "wins": r["wins"],
                        "losses": r["losses"],
                        "gate_decision": r["gate_decision"],
                        "status": r["status"],
                        "evaluator_epoch": r.get("evaluator_epoch"),
                        "evaluator_epoch_status": r["evaluator_epoch_status"],
                        "quarantined": bool(r.get("quarantined")),
                    }
                    for r in rows
                ],
            }
            write_json_stdout(payload)
            return

        console.print(f"[bold]Run {run_id}[/bold]  scenario={run.get('scenario')!r}  status={run.get('status')!r}")
        if not rows:
            console.print("[yellow]No generations recorded for this run.[/yellow]")
            return
        table = Table(title="Generations" + (" (best)" if best else ""))
        table.add_column("Gen")
        table.add_column("Mean")
        table.add_column("Best")
        table.add_column("Elo")
        table.add_column("W")
        table.add_column("L")
        table.add_column("Gate")
        table.add_column("Status")
        table.add_column("Lineage")
        for row in rows:
            table.add_row(
                str(row["generation_index"]),
                f"{row['mean_score']:.4f}",
                f"{row['best_score']:.4f}",
                f"{row['elo']:.2f}",
                str(row["wins"]),
                str(row["losses"]),
                row["gate_decision"],
                row["status"],
                _lineage_cell(row),
            )
        console.print(table)
        warning = _stale_warning_line(rows, active_epoch_id)
        if warning is not None:
            console.print(warning)

    @app.command()
    def watch(
        run_id: str = typer.Argument(...),
        interval: float = typer.Option(2.0, "--interval", min=0.1, help="Poll interval in seconds"),
        json_output: bool = typer.Option(False, "--json", help="Emit one JSONL row per poll"),
    ) -> None:
        """Stream live status updates for an in-flight run."""
        load_settings = _cli_attr(dependency_module, "load_settings")
        sqlite_from_settings = _cli_attr(dependency_module, "_sqlite_from_settings")
        write_json_stderr = _cli_attr(dependency_module, "_write_json_stderr")

        settings = load_settings()
        store = sqlite_from_settings(settings)
        if store.get_run(run_id) is None:
            message = f"run {run_id!r} not found"
            if json_output:
                write_json_stderr(message)
            else:
                console.print(f"[red]{message}[/red]")
            raise typer.Exit(code=1)

        last_emit: tuple[int, str] | None = None
        while True:
            # PR #1002 review (P2): re-read the run row each iteration
            # so a terminal run with no generation rows (e.g. failed at
            # spec-load time) breaks the loop instead of polling
            # forever. Previously only the latest generation's status
            # was checked, which never fired when `run_status` is
            # empty.
            run = store.get_run(run_id)
            run_status = str((run or {}).get("status", "")).lower()

            rows = store.run_status(run_id)
            latest = rows[-1] if rows else None
            if latest is not None:
                current = (int(latest["generation_index"]), str(latest["status"]))
                if current != last_emit:
                    if json_output:
                        sys.stdout.write(
                            json.dumps(
                                {
                                    "run_id": run_id,
                                    "generation": latest["generation_index"],
                                    "status": latest["status"],
                                    "best_score": latest["best_score"],
                                    "gate_decision": latest["gate_decision"],
                                }
                            )
                            + "\n"
                        )
                        sys.stdout.flush()
                    else:
                        console.print(
                            f"gen={latest['generation_index']} status={latest['status']} "
                            f"best={latest['best_score']:.4f} gate={latest['gate_decision']}"
                        )
                    last_emit = current
                if str(latest["status"]).lower() in _TERMINAL_STATUSES:
                    return
            elif run_status in _TERMINAL_STATUSES:
                # No generation rows yet but the run row itself is
                # terminal -> emit a final status and exit.
                if json_output:
                    sys.stdout.write(
                        json.dumps(
                            {
                                "run_id": run_id,
                                "generation": None,
                                "status": run_status,
                            }
                        )
                        + "\n"
                    )
                    sys.stdout.flush()
                else:
                    console.print(f"run status={run_status} (no generations recorded)")
                return
            time.sleep(interval)
