from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from autocontext.consultation.runner import ConsultationRunner
from autocontext.consultation.types import ConsultationRequest as ConsReq
from autocontext.consultation.types import ConsultationTrigger
from autocontext.providers.base import LLMProvider
from autocontext.providers.registry import create_provider
from autocontext.providers.retry import RetryProvider
from autocontext.server.changelog import build_changelog
from autocontext.server.writeup import generate_writeup
from autocontext.storage.artifacts import ArtifactStore
from autocontext.storage.sqlite_store import SQLiteStore

LOGGER = logging.getLogger(__name__)

cockpit_router = APIRouter(prefix="/api/cockpit", tags=["cockpit"])


def _get_store(request: Request) -> SQLiteStore:
    store = getattr(request.app.state, "store", None)
    if not isinstance(store, SQLiteStore):
        raise HTTPException(status_code=500, detail="Application store is not configured")
    return store


def _get_artifacts(request: Request) -> ArtifactStore:
    settings = getattr(request.app.state, "app_settings", None)
    if settings is None:
        raise HTTPException(status_code=500, detail="Application settings are not configured")
    return ArtifactStore(
        runs_root=settings.runs_root,
        knowledge_root=settings.knowledge_root,
        skills_root=settings.skills_root,
        claude_skills_path=settings.claude_skills_path,
    )


@cockpit_router.get("/runs")
def list_runs(request: Request) -> list[dict[str, Any]]:
    """List recent runs with summary info."""
    store = _get_store(request)
    with store.connect() as conn:
        runs = conn.execute(
            "SELECT run_id, scenario, target_generations, status, created_at, updated_at "
            "FROM runs ORDER BY created_at DESC LIMIT 50"
        ).fetchall()

    result: list[dict[str, Any]] = []
    for run in runs:
        run_dict = dict(run)
        run_id = run_dict["run_id"]
        scenario = run_dict["scenario"]

        # Get generation summary
        with store.connect() as conn:
            gen_rows = conn.execute(
                "SELECT generation_index, best_score, elo, duration_seconds "
                "FROM generations WHERE run_id = ? ORDER BY generation_index",
                (run_id,),
            ).fetchall()

        generations_completed = len(gen_rows)
        best_score = max((g["best_score"] for g in gen_rows), default=0.0)
        best_elo = max((g["elo"] for g in gen_rows), default=0.0)
        total_duration = sum(g["duration_seconds"] or 0.0 for g in gen_rows)

        result.append({
            "run_id": run_id,
            "scenario_name": scenario,
            "generations_completed": generations_completed,
            "best_score": best_score,
            "best_elo": best_elo,
            "status": run_dict["status"],
            "created_at": run_dict["created_at"],
            "duration_seconds": round(total_duration, 1),
        })

    return result


@cockpit_router.get("/runs/{run_id}/status")
def run_status(run_id: str, request: Request) -> dict[str, Any]:
    """Detailed run status with generation-level breakdown."""
    store = _get_store(request)

    with store.connect() as conn:
        run_row = conn.execute(
            "SELECT run_id, scenario, target_generations, status, created_at "
            "FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()

    if not run_row:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    run_dict = dict(run_row)

    with store.connect() as conn:
        gen_rows = conn.execute(
            "SELECT generation_index, mean_score, best_score, elo, wins, losses, "
            "gate_decision, status, duration_seconds "
            "FROM generations WHERE run_id = ? ORDER BY generation_index ASC",
            (run_id,),
        ).fetchall()

    generations = []
    for g in gen_rows:
        gd = dict(g)
        generations.append({
            "generation": gd["generation_index"],
            "mean_score": gd["mean_score"],
            "best_score": gd["best_score"],
            "elo": gd["elo"],
            "wins": gd["wins"],
            "losses": gd["losses"],
            "gate_decision": gd["gate_decision"],
            "status": gd["status"],
            "duration_seconds": gd["duration_seconds"],
        })

    return {
        "run_id": run_id,
        "scenario_name": run_dict["scenario"],
        "target_generations": run_dict["target_generations"],
        "status": run_dict["status"],
        "created_at": run_dict["created_at"],
        "generations": generations,
    }


@cockpit_router.get("/runs/{run_id}/changelog")
def changelog(run_id: str, request: Request) -> dict[str, Any]:
    """What changed between consecutive generations."""
    store = _get_store(request)
    artifacts = _get_artifacts(request)
    return build_changelog(run_id, store, artifacts)


@cockpit_router.get("/runs/{run_id}/compare/{gen_a}/{gen_b}")
def compare_generations(run_id: str, gen_a: int, gen_b: int, request: Request) -> dict[str, Any]:
    """Compare two generations side-by-side."""
    store = _get_store(request)

    with store.connect() as conn:
        row_a = conn.execute(
            "SELECT generation_index, mean_score, best_score, elo, gate_decision "
            "FROM generations WHERE run_id = ? AND generation_index = ?",
            (run_id, gen_a),
        ).fetchone()
        row_b = conn.execute(
            "SELECT generation_index, mean_score, best_score, elo, gate_decision "
            "FROM generations WHERE run_id = ? AND generation_index = ?",
            (run_id, gen_b),
        ).fetchone()

    if not row_a:
        raise HTTPException(status_code=404, detail=f"Generation {gen_a} not found for run '{run_id}'")
    if not row_b:
        raise HTTPException(status_code=404, detail=f"Generation {gen_b} not found for run '{run_id}'")

    da = dict(row_a)
    db = dict(row_b)

    return {
        "gen_a": {
            "generation": da["generation_index"],
            "mean_score": da["mean_score"],
            "best_score": da["best_score"],
            "elo": da["elo"],
            "gate_decision": da["gate_decision"],
        },
        "gen_b": {
            "generation": db["generation_index"],
            "mean_score": db["mean_score"],
            "best_score": db["best_score"],
            "elo": db["elo"],
            "gate_decision": db["gate_decision"],
        },
        "score_delta": round(db["best_score"] - da["best_score"], 6),
        "elo_delta": round(db["elo"] - da["elo"], 6),
    }


@cockpit_router.get("/runs/{run_id}/resume")
def resume_info(run_id: str, request: Request) -> dict[str, Any]:
    """Resume affordances for a run."""
    store = _get_store(request)

    with store.connect() as conn:
        run_row = conn.execute(
            "SELECT run_id, scenario, target_generations, status FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()

    if not run_row:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    run_dict = dict(run_row)
    status = run_dict["status"]
    target = run_dict["target_generations"]

    with store.connect() as conn:
        gen_rows = conn.execute(
            "SELECT generation_index, gate_decision FROM generations "
            "WHERE run_id = ? ORDER BY generation_index DESC LIMIT 1",
            (run_id,),
        ).fetchall()

    last_gen = gen_rows[0]["generation_index"] if gen_rows else 0
    last_gate = gen_rows[0]["gate_decision"] if gen_rows else ""

    can_resume = status == "running" and last_gen < target
    if status == "completed":
        hint = "Run completed successfully. Start a new run to continue exploration."
    elif status == "running" and last_gen >= target:
        hint = "All target generations completed. Mark as complete or increase target."
        can_resume = False
    elif status == "running":
        hint = f"Run in progress. Resume from generation {last_gen + 1}."
    else:
        hint = f"Run status is '{status}'."

    return {
        "run_id": run_id,
        "status": status,
        "last_generation": last_gen,
        "last_gate_decision": last_gate,
        "can_resume": can_resume,
        "resume_hint": hint,
    }


@cockpit_router.get("/writeup/{run_id}")
def writeup(run_id: str, request: Request) -> dict[str, Any]:
    """Lightweight writeup assembled from existing artifacts."""
    store = _get_store(request)
    artifacts = _get_artifacts(request)

    with store.connect() as conn:
        run_row = conn.execute(
            "SELECT run_id, scenario FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()

    if not run_row:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    run_dict = dict(run_row)
    md = generate_writeup(run_id, store, artifacts)

    return {
        "run_id": run_id,
        "scenario_name": run_dict["scenario"],
        "writeup_markdown": md,
    }


# ---------------------------------------------------------------------------
# Operator-requested consultation (AC-220)
# ---------------------------------------------------------------------------


class ConsultationRequestBody(BaseModel):
    context_summary: str = ""
    generation: int | None = None


def _create_cockpit_consultation_provider(settings: Any) -> LLMProvider | None:
    """Create consultation provider from settings, or None if not configured."""
    if not settings.consultation_api_key:
        return None
    return create_provider(
        provider_type=settings.consultation_provider,
        api_key=settings.consultation_api_key,
        base_url=settings.consultation_base_url or None,
        model=settings.consultation_model,
    )


@cockpit_router.post("/runs/{run_id}/consult")
def request_consultation(run_id: str, body: ConsultationRequestBody, request: Request) -> dict[str, Any]:
    """Request an explicit operator consultation for a run."""
    store = _get_store(request)
    settings = getattr(request.app.state, "app_settings", None)
    if settings is None:
        raise HTTPException(status_code=500, detail="Settings not configured")

    if not settings.consultation_enabled:
        raise HTTPException(status_code=400, detail="Consultation is not enabled")

    # Validate run exists
    with store.connect() as conn:
        run_row = conn.execute("SELECT run_id, scenario FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if not run_row:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    # Determine generation
    generation = body.generation
    if generation is None:
        with store.connect() as conn:
            gen_row = conn.execute(
                "SELECT MAX(generation_index) as max_gen FROM generations WHERE run_id = ?", (run_id,)
            ).fetchone()
            generation = gen_row["max_gen"] if gen_row and gen_row["max_gen"] is not None else 0

    # Check cost budget
    if settings.consultation_cost_budget > 0:
        spent = store.get_total_consultation_cost(run_id)
        if spent >= settings.consultation_cost_budget:
            raise HTTPException(
                status_code=429,
                detail=f"Consultation budget exceeded (spent ${spent:.2f} of ${settings.consultation_cost_budget:.2f})",
            )

    # Build provider
    provider = _create_cockpit_consultation_provider(settings)
    if provider is None:
        raise HTTPException(status_code=503, detail="Consultation provider not configured (missing API key)")

    # Gather context from generations
    with store.connect() as conn:
        gen_rows = conn.execute(
            "SELECT generation_index, mean_score, best_score, elo, gate_decision "
            "FROM generations WHERE run_id = ? ORDER BY generation_index ASC",
            (run_id,),
        ).fetchall()

    score_history = [float(g["best_score"]) for g in gen_rows]
    gate_history = [str(g["gate_decision"]) for g in gen_rows]

    # Get current best strategy summary
    strategy_summary = ""
    with store.connect() as conn:
        strat_row = conn.execute(
            "SELECT content FROM agent_outputs WHERE run_id = ? AND role = 'competitor' "
            "ORDER BY generation_index DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        if strat_row:
            strategy_summary = str(strat_row["content"])[:500]

    # Build consultation request
    context = body.context_summary or f"Operator-requested consultation for run {run_id} at generation {generation}"

    cons_request = ConsReq(
        run_id=run_id,
        generation=generation,
        trigger=ConsultationTrigger.OPERATOR_REQUEST,
        context_summary=context,
        current_strategy_summary=strategy_summary,
        score_history=score_history,
        gate_history=gate_history,
    )

    runner = ConsultationRunner(RetryProvider(provider))

    try:
        result = runner.consult(cons_request)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Consultation call failed: {exc}") from exc

    # Persist
    row_id = store.insert_consultation(
        run_id=run_id,
        generation_index=generation,
        trigger=ConsultationTrigger.OPERATOR_REQUEST.value,
        context_summary=context,
        critique=result.critique,
        alternative_hypothesis=result.alternative_hypothesis,
        tiebreak_recommendation=result.tiebreak_recommendation,
        suggested_next_action=result.suggested_next_action,
        raw_response=result.raw_response,
        model_used=result.model_used,
        cost_usd=result.cost_usd,
    )

    # Write advisory artifact
    artifacts = _get_artifacts(request)
    advisory_dir = artifacts.generation_dir(run_id, generation)
    advisory_path = advisory_dir / "operator_consultation.md"
    artifacts.write_markdown(advisory_path, result.to_advisory_markdown())

    return {
        "consultation_id": row_id,
        "run_id": run_id,
        "generation": generation,
        "trigger": "operator_request",
        "critique": result.critique,
        "alternative_hypothesis": result.alternative_hypothesis,
        "tiebreak_recommendation": result.tiebreak_recommendation,
        "suggested_next_action": result.suggested_next_action,
        "model_used": result.model_used,
        "cost_usd": result.cost_usd,
        "advisory_markdown": result.to_advisory_markdown(),
    }


@cockpit_router.get("/runs/{run_id}/consultations")
def list_consultations(run_id: str, request: Request) -> list[dict[str, Any]]:
    """List all consultations for a run."""
    store = _get_store(request)
    return store.get_consultations_for_run(run_id)
