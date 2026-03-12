from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from autocontext.server.changelog import build_changelog
from autocontext.server.writeup import generate_writeup
from autocontext.storage.artifacts import ArtifactStore
from autocontext.storage.sqlite_store import SQLiteStore

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
