"""Human-only acquisition and annotation commands over existing calibration storage."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

import typer

from autocontext.analytics.calibration import (
    AcquisitionCandidate,
    AcquisitionPolicy,
    CalibrationStore,
    ProtectedMembership,
    export_acquisition_labels,
    pending_acquisition_samples,
    reconcile_acquisition_round,
    record_acquisition_review,
    select_acquisition_round,
)
from autocontext.config import load_settings
from autocontext.storage.sqlite_store import SQLiteStore
from autocontext.util.json_io import read_json

labels_app = typer.Typer(help="Select and review human evaluator labels without model-authored ground truth.")


def _stores(root: Path | None, db_path: Path | None) -> tuple[CalibrationStore, SQLiteStore]:
    calibration = CalibrationStore(root if root is not None else load_settings().knowledge_root / "analytics")
    sqlite = SQLiteStore(db_path if db_path is not None else load_settings().db_path)
    sqlite.ensure_core_tables()
    return calibration, sqlite


def _protected(path: Path) -> ProtectedMembership:
    return ProtectedMembership.model_validate(read_json(path))


@labels_app.command("select")
def select_labels(
    pool: Annotated[Path, typer.Option("--pool", help="JSON array of candidate tasks and judge signals")],
    protected: Annotated[Path, typer.Option("--protected", help="Current protected group/content membership JSON")],
    round_id: Annotated[str, typer.Option("--round-id", help="New immutable selection round identity")],
    budget: Annotated[int, typer.Option("--budget", min=1, max=250)] = 10,
    audit_fraction: Annotated[float, typer.Option("--audit-fraction", min=0, max=1)] = 0.2,
    boundary: Annotated[float, typer.Option("--boundary", min=0, max=1)] = 0.5,
    seed: Annotated[int, typer.Option("--seed", min=0)] = 1023,
    root: Annotated[Path | None, typer.Option("--root", help="Calibration storage root override")] = None,
) -> None:
    """Freeze a reproducible targeted/random-audit queue; this creates no human labels."""
    raw = read_json(pool)
    if not isinstance(raw, list):
        raise typer.BadParameter("candidate pool must be a JSON array")
    cases = [AcquisitionCandidate.model_validate(value) for value in raw]
    calibration = CalibrationStore(root or load_settings().knowledge_root / "analytics")
    rnd = select_acquisition_round(cases, AcquisitionPolicy(
        max_labels=budget, random_audit_fraction=audit_fraction, acceptance_boundary=boundary, seed=seed),
        _protected(protected), round_id=round_id)
    calibration.persist_acquisition_round(rnd)
    typer.echo(json.dumps({"round_id": round_id, "selected": len(rnd.samples), "summary": rnd.summary}))


@labels_app.command("pending")
def pending_labels(
    round_id: Annotated[str, typer.Argument(help="Existing selection round")],
    root: Annotated[Path | None, typer.Option("--root")] = None,
    db_path: Annotated[Path | None, typer.Option("--db-path")] = None,
) -> None:
    """Reconcile interrupted writes and show unreviewed or disputed tasks."""
    calibration, sqlite = _stores(root, db_path)
    reconcile_acquisition_round(calibration, sqlite, round_id)
    typer.echo(json.dumps([sample.to_dict() for sample in pending_acquisition_samples(calibration, round_id)], indent=2))


@labels_app.command("review")
def review_label(
    round_id: Annotated[str, typer.Argument(help="Existing selection round")],
    sample_id: Annotated[str, typer.Argument(help="Selected sample id")],
    protected: Annotated[Path, typer.Option("--protected", help="Recheck current protected membership")],
    by: Annotated[str, typer.Option("--by", help="Human reviewer identity")],
    decision: Annotated[Literal["label", "skip", "correct", "disagree"], typer.Option("--decision")],
    score: Annotated[float | None, typer.Option("--score", min=0, max=1)] = None,
    rationale: Annotated[str, typer.Option("--rationale")] = "",
    criterion_scores: Annotated[Path | None, typer.Option("--criterion-scores", help="JSON rubric criterion scores")] = None,
    root: Annotated[Path | None, typer.Option("--root")] = None,
    db_path: Annotated[Path | None, typer.Option("--db-path")] = None,
) -> None:
    """Record an explicit human decision, or reconcile an interrupted review."""
    calibration, sqlite = _stores(root, db_path)
    scores = read_json(criterion_scores) if criterion_scores is not None else None
    if scores is not None and not isinstance(scores, dict):
        raise typer.BadParameter("criterion scores must be a JSON object")
    outcome = record_acquisition_review(calibration, sqlite, round_id, sample_id, reviewer=by,
                                         decision=decision, human_score=score, rationale=rationale,
                                         criterion_scores=scores, protected=_protected(protected))
    typer.echo(json.dumps({"outcome_id": outcome.outcome_id, "decision": outcome.decision,
                           "reviewer": outcome.reviewer}))


@labels_app.command("export")
def export_labels(
    round_id: Annotated[str, typer.Argument(help="Existing selection round")],
    protected: Annotated[Path, typer.Option("--protected", help="Current protected membership")],
    output: Annotated[Path | None, typer.Option("--output", help="Optional JSON export file")] = None,
    root: Annotated[Path | None, typer.Option("--root")] = None,
    db_path: Annotated[Path | None, typer.Option("--db-path")] = None,
) -> None:
    """Export only reviewed training/development labels; never infer population accuracy."""
    calibration, sqlite = _stores(root, db_path)
    reconcile_acquisition_round(calibration, sqlite, round_id)
    result = export_acquisition_labels(calibration, sqlite, round_id, protected=_protected(protected))
    if output is None:
        typer.echo(json.dumps(result, indent=2))
    else:
        try:
            with output.open("x", encoding="utf-8") as stream:
                json.dump(result, stream, indent=2)
        except FileExistsError as exc:
            raise typer.BadParameter("export path already exists; refuse overwrite") from exc
        typer.echo(json.dumps({"output": str(output), "summary": result["summary"]}))
