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

if TYPE_CHECKING:
    from rich.console import Console

_TERMINAL_STATUSES = frozenset({"completed", "failed", "succeeded", "errored"})


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

        if json_output:
            payload: dict[str, Any] = {
                "run_id": run_id,
                "scenario": run.get("scenario"),
                "status": run.get("status"),
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
            )
        console.print(table)

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
