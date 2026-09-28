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

# The replay ladder's keys (prescreen.models.MODEL_FACTORIES), listed here so that validating them needs no scikit-learn.
MODEL_KEYS = ("p0", "p1", "p2", "p3")


def _to_stderr(message: str) -> None:
    typer.echo(message, err=True)


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
    """Count eligible judged rounds per judge identity and scenario family, and the value ceiling."""
    rows = [asdict(row) for row in sufficiency_report(_rounds(_store(db_path)))]
    path = write_json_report(_report_dir(out), "sufficiency", {"generated_at": datetime.now(UTC).isoformat(), "rows": rows})
    typer.echo(json.dumps({"report_path": str(path), "rows": rows}, indent=2))
    _to_stderr(f"wrote {path}")


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-") or "family"


def _model_keys(models: str) -> list[str]:
    keys = [key.strip() for key in models.split(",") if key.strip()]
    if not keys or any(key not in MODEL_KEYS for key in keys):
        raise typer.BadParameter(f"give comma-separated keys from {', '.join(MODEL_KEYS)}; got {models!r}", param_hint="--models")
    return keys


@prescreen_app.command("replay")
def replay(
    family: Annotated[str, typer.Option("--family", help="Scenario family, for example datagen or agent_task")],
    judge_identity: Annotated[
        str, typer.Option("--judge-identity", help="Judge identity whose verdicts are replayed (from the sufficiency report)")
    ],
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
    """Replay the model ladder offline on one family and judge identity, and evaluate the Phase 1 gate."""
    model_keys = _model_keys(models)
    if curve_model != "none" and curve_model not in MODEL_KEYS:
        raise typer.BadParameter(
            f"give one key from {', '.join(MODEL_KEYS)}, or 'none'; got {curve_model!r}", param_hint="--curve-model"
        )
    try:
        from autocontext.prescreen.replay import run_replay
    except ImportError:
        typer.echo("autoctx prescreen replay needs the prescreen extra: pip install 'autocontext[prescreen]'", err=True)
        raise typer.Exit(2) from None
    examples = build_examples(eligible_rounds(_rounds(_store(db_path))), family=family, judge_identity=judge_identity)
    if not examples:
        typer.echo(f"no eligible rounds for family {family!r} and judge identity {judge_identity!r}", err=True)
        raise typer.Exit(1)
    report = run_replay(
        examples,
        family=family,
        judge_identity=judge_identity,
        model_keys=model_keys,
        curve_model=None if curve_model == "none" else curve_model,
        blocks=blocks,
        min_train=min_train,
        curve_halvings=curve_halvings,
        curve_repeats=curve_repeats,
        seed=seed,
        resamples=resamples,
    )
    path = write_json_report(_report_dir(out), f"replay-{_slug(family)}", report)
    document = {"report_path": str(path), "gate": report["gate"], "summary": report["summary"]}
    typer.echo(json.dumps(document, indent=2, default=str))
    _to_stderr(f"wrote {path}")


@prescreen_app.command("datagen")
def datagen(
    protocol: Annotated[Path, typer.Argument(help="Committed protocol.json (the pre-registration)")],
    out: Annotated[Path, typer.Option("--out", help="New output directory; it must not exist")],
    db_path: Annotated[Path | None, typer.Option("--db-path")] = None,
) -> None:
    """Run the pre-registered data-generation protocol with the judge ledger on, under its call, token and cost caps.

    The summary is the one JSON document on stdout; progress goes to stderr.
    """
    from autocontext.prescreen.datagen import load_protocol, run_datagen
    from autocontext.providers.registry import get_provider

    try:
        spec, raw = load_protocol(protocol)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="PROTOCOL") from None
    summary = run_datagen(
        spec, raw, out_dir=out, store=_store(db_path), provider=get_provider(load_settings()), log=_to_stderr
    )
    typer.echo(json.dumps(asdict(summary), indent=2))
