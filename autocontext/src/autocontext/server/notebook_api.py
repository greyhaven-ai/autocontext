"""REST API router for session notebooks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from autocontext.config import load_settings
from autocontext.storage.artifacts import ArtifactStore
from autocontext.storage.sqlite_store import SQLiteStore

notebook_router = APIRouter(prefix="/api/notebooks", tags=["notebooks"])


class NotebookBody(BaseModel):
    scenario_name: str | None = None
    current_objective: str | None = None
    current_hypotheses: list[str] | None = None
    best_run_id: str | None = None
    best_generation: int | None = None
    best_score: float | None = None
    unresolved_questions: list[str] | None = None
    operator_observations: list[str] | None = None
    follow_ups: list[str] | None = None


def _get_store(request: Request) -> SQLiteStore:
    store = getattr(request.app.state, "store", None)
    if store is not None:
        return store  # type: ignore[no-any-return]
    settings = getattr(request.app.state, "app_settings", None) or load_settings()
    return SQLiteStore(settings.db_path)


def _get_artifacts(request: Request) -> ArtifactStore:
    settings = getattr(request.app.state, "app_settings", None) or load_settings()
    return ArtifactStore(
        runs_root=settings.runs_root,
        knowledge_root=settings.knowledge_root,
        skills_root=settings.skills_root,
        claude_skills_path=settings.claude_skills_path,
    )


@notebook_router.get("/")
def list_notebooks(request: Request) -> list[dict[str, Any]]:
    store = _get_store(request)
    return store.list_notebooks()


@notebook_router.get("/{session_id}")
def get_notebook(session_id: str, request: Request) -> dict[str, Any]:
    store = _get_store(request)
    nb = store.get_notebook(session_id)
    if nb is None:
        raise HTTPException(status_code=404, detail=f"Notebook not found: {session_id}")
    return nb


@notebook_router.put("/{session_id}")
def upsert_notebook(session_id: str, body: NotebookBody, request: Request) -> dict[str, Any]:
    store = _get_store(request)
    existing = store.get_notebook(session_id)
    scenario_name = body.scenario_name or (str(existing["scenario_name"]) if existing is not None else None)
    if not scenario_name:
        raise HTTPException(status_code=400, detail="scenario_name is required when creating a notebook")
    store.upsert_notebook(
        session_id=session_id,
        scenario_name=scenario_name,
        current_objective=body.current_objective,
        current_hypotheses=body.current_hypotheses,
        best_run_id=body.best_run_id,
        best_generation=body.best_generation,
        best_score=body.best_score,
        unresolved_questions=body.unresolved_questions,
        operator_observations=body.operator_observations,
        follow_ups=body.follow_ups,
    )
    # Sync to filesystem
    nb = store.get_notebook(session_id)
    if nb is not None:
        artifacts = _get_artifacts(request)
        artifacts.write_notebook(session_id, nb)

    # Emit event
    _emit_notebook_event(request, session_id, scenario_name)

    return nb or {"session_id": session_id, "scenario_name": scenario_name}


@notebook_router.delete("/{session_id}")
def delete_notebook(session_id: str, request: Request) -> dict[str, str]:
    store = _get_store(request)
    deleted = store.delete_notebook(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Notebook not found: {session_id}")
    artifacts = _get_artifacts(request)
    artifacts.delete_notebook(session_id)
    return {"status": "deleted", "session_id": session_id}


def _emit_notebook_event(request: Request, session_id: str, scenario_name: str) -> None:
    """Emit notebook_updated event if event stream is configured."""
    settings = getattr(request.app.state, "app_settings", None)
    if settings is None:
        return
    event_path: Path = settings.event_stream_path
    if not event_path.parent.exists():
        return
    from autocontext.loop.events import EventStreamEmitter

    emitter = EventStreamEmitter(event_path)
    emitter.emit(
        "notebook_updated",
        {"session_id": session_id, "scenario_name": scenario_name},
        channel="notebook",
    )
