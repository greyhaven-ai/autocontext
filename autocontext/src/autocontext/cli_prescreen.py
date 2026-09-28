"""`autoctx prescreen`: judge pre-screen tooling (docs/internal/judge-prescreen-design.md)."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from autocontext.config import load_settings
from autocontext.prescreen.dataset import build_examples
from autocontext.prescreen.rounds import LedgerRound, eligible_rounds
from autocontext.prescreen.sufficiency import sufficiency_report, write_json_report
from autocontext.storage.sqlite_store import SQLiteStore

prescreen_app = typer.Typer(help="Judge pre-screen: capture sufficiency, offline replay and pre-registered data generation.")


def _store(db_path: Path | None) -> SQLiteStore:
    store = SQLiteStore(db_path if db_path is not None else load_settings().db_path)
    store.ensure_core_tables()
    return store


def _report_dir(out: Path | None) -> Path:
    return out if out is not None else load_settings().knowledge_root / "analytics" / "prescreen"


def _rounds(store: SQLiteStore) -> list[LedgerRound]:
    return [LedgerRound.from_row(row) for row in store.list_judge_ledger_rows()]


@prescreen_app.command("sufficiency")
def sufficiency(
    db_path: Annotated[Path | None, typer.Option("--db-path")] = None,
    out: Annotated[Path | None, typer.Option("--out", help="Report directory")] = None,
) -> None:
    """Count eligible judged rounds per evaluator epoch and scenario family, and the value ceiling."""
    rows = [asdict(row) for row in sufficiency_report(_rounds(_store(db_path)))]
    path = write_json_report(_report_dir(out), "sufficiency", {"generated_at": datetime.now(UTC).isoformat(), "rows": rows})
    typer.echo(json.dumps(rows, indent=2))
    typer.echo(f"wrote {path}")


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-") or "family"


@prescreen_app.command("replay")
def replay(
    family: Annotated[str, typer.Option("--family", help="Scenario family, for example datagen or agent_task")],
    epoch: Annotated[str, typer.Option("--epoch", help="Evaluator epoch whose verdicts are replayed")],
    models: Annotated[str, typer.Option("--models")] = "p0,p1,p2,p3",
    curve_model: Annotated[str, typer.Option("--curve-model", help="Model for the learning curve; 'none' skips it")] = "p3",
    curve_halvings: Annotated[int, typer.Option("--curve-halvings")] = 3,
    curve_repeats: Annotated[int, typer.Option("--curve-repeats")] = 5,
    blocks: Annotated[int, typer.Option("--blocks")] = 5,
    min_train: Annotated[int, typer.Option("--min-train")] = 100,
    seed: Annotated[int, typer.Option("--seed")] = 0,
    resamples: Annotated[int, typer.Option("--resamples")] = 2000,
    db_path: Annotated[Path | None, typer.Option("--db-path")] = None,
    out: Annotated[Path | None, typer.Option("--out", help="Report directory")] = None,
) -> None:
    """Replay the model ladder offline on one family and epoch, and evaluate the Phase 1 gate."""
    try:
        from autocontext.prescreen.replay import run_replay
    except ImportError:
        typer.echo("autoctx prescreen replay needs the prescreen extra: pip install 'autocontext[prescreen]'", err=True)
        raise typer.Exit(2) from None
    examples = build_examples(eligible_rounds(_rounds(_store(db_path))), family=family, epoch=epoch)
    if not examples:
        typer.echo(f"no eligible rounds for family {family!r} and epoch {epoch!r}", err=True)
        raise typer.Exit(1)
    report = run_replay(
        examples,
        family=family,
        epoch=epoch,
        model_keys=[m.strip() for m in models.split(",") if m.strip()],
        curve_model=None if curve_model == "none" else curve_model,
        blocks=blocks,
        min_train=min_train,
        curve_halvings=curve_halvings,
        curve_repeats=curve_repeats,
        seed=seed,
        resamples=resamples,
    )
    path = write_json_report(_report_dir(out), f"replay-{_slug(family)}", report)
    typer.echo(json.dumps({"gate": report["gate"], "summary": report["summary"]}, indent=2, default=str))
    typer.echo(f"wrote {path}")
