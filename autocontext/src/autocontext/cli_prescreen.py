"""`autoctx prescreen`: judge pre-screen tooling (docs/internal/judge-prescreen-design.md)."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from autocontext.config import load_settings
from autocontext.prescreen.rounds import LedgerRound
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
