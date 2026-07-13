"""HTTP run-status generation view with evaluator-epoch lineage (AC-885 Slice D1).

Extracted from ``cockpit_api`` so that module stays under its size limit: this holds the
generation-dict construction plus the read-only epoch-lineage annotation and stale-epoch warnings for
``GET /runs/{run_id}/status``. Adds no score, judge call, or promotion; it reads the scenario's active
epoch via ``EvaluatorEpochRegistry.active_for`` (whose own contract may lock and self-heal) and renders.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from autocontext.execution.epoch_lineage import annotate_status_rows, revision_fields
from autocontext.execution.evaluator_epoch_registry import EvaluatorEpochRegistry
from autocontext.storage.sqlite_store import SQLiteStore


def _epoch_registry_from_request(request: Request) -> EvaluatorEpochRegistry:
    settings = getattr(request.app.state, "app_settings", None)
    if settings is None:
        raise HTTPException(status_code=500, detail="Application settings are not configured")
    return EvaluatorEpochRegistry(settings.knowledge_root / "_evaluator_epochs")


def build_run_status_generations(
    gen_rows: list[Any],
    scenario: str | None,
    request: Request,
    store: SQLiteStore,
    run_id: str,
) -> tuple[list[dict[str, Any]], str | None, list[dict[str, Any]]]:
    """Return (generation dicts with lineage, active epoch id, stale-epoch warnings).

    Each generation dict carries the existing status fields plus ``evaluator_epoch``,
    ``quarantined`` (bool), and ``evaluator_epoch_status`` (one of current/stale/unknown/no_active_epoch).
    A ``stale_epoch`` warning is emitted only for generations classified ``stale``.
    """
    generations: list[dict[str, Any]] = []
    for row in gen_rows:
        gd = dict(row)
        generations.append(
            {
                "generation": gd["generation_index"],
                "mean_score": gd["mean_score"],
                "best_score": gd["best_score"],
                "elo": gd["elo"],
                "wins": gd["wins"],
                "losses": gd["losses"],
                "gate_decision": gd["gate_decision"],
                "status": gd["status"],
                "duration_seconds": gd["duration_seconds"],
                "evaluator_epoch": gd["evaluator_epoch"],
                "quarantined": bool(gd["quarantined"]),
            }
        )

    generations, active_id = annotate_status_rows(generations, scenario, _epoch_registry_from_request(request))
    revs = store.latest_active_revisions(run_id, active_id)
    for g in generations:
        g.update(revision_fields(revs.get(g["generation"])))
    warnings = [
        {
            "warning_type": "stale_epoch",
            "generation": g["generation"],
            "evaluator_epoch": g.get("evaluator_epoch"),
            "active_evaluator_epoch": active_id,
            "description": f"generation {g['generation']} scored under a stale evaluator epoch",
        }
        for g in generations
        if g.get("evaluator_epoch_status") == "stale"
    ]
    return generations, active_id, warnings
