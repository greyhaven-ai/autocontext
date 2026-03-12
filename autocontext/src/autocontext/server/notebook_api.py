"""REST API router for session notebooks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from autocontext.config import load_settings
from autocontext.storage.artifacts import ArtifactStore
from autocontext.storage.sqlite_store import SQLiteStore

notebook_router = APIRouter(prefix="/api/notebooks", tags=["notebooks"])


class NotebookBody(BaseModel):
    current_objective: str = ""
    current_hypotheses: list[str] = Field(default_factory=list)
    best_run_id: str | None = None
    best_generation: int | None = None
    best_score: float | None = None
    unresolved_questions: list[str] = Field(default_factory=list)
    operator_observations: list[str] = Field(default_factory=list)
    follow_ups: list[str] = Field(default_factory=list)


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


@notebook_router.get("/{scenario_name}")
def get_notebook(scenario_name: str, request: Request) -> dict[str, Any]:
    store = _get_store(request)
    nb = store.get_notebook(scenario_name)
    if nb is None:
        raise HTTPException(status_code=404, detail=f"Notebook not found: {scenario_name}")
    return nb


@notebook_router.put("/{scenario_name}")
def upsert_notebook(scenario_name: str, body: NotebookBody, request: Request) -> dict[str, Any]:
    store = _get_store(request)
    store.upsert_notebook(
        scenario_name=scenario_name,
        current_objective=body.current_objective,
        current_hypotheses=body.current_hypotheses if body.current_hypotheses else None,
        best_run_id=body.best_run_id,
        best_generation=body.best_generation,
        best_score=body.best_score,
        unresolved_questions=body.unresolved_questions if body.unresolved_questions else None,
        operator_observations=body.operator_observations if body.operator_observations else None,
        follow_ups=body.follow_ups if body.follow_ups else None,
    )
    # Sync to filesystem
    nb = store.get_notebook(scenario_name)
    if nb is not None:
        artifacts = _get_artifacts(request)
        artifacts.write_notebook(scenario_name, nb)

    # Emit event
    _emit_notebook_event(request, scenario_name)

    return nb or {"scenario_name": scenario_name}


@notebook_router.delete("/{scenario_name}")
def delete_notebook(scenario_name: str, request: Request) -> dict[str, str]:
    store = _get_store(request)
    deleted = store.delete_notebook(scenario_name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Notebook not found: {scenario_name}")
    return {"status": "deleted", "scenario_name": scenario_name}


def _emit_notebook_event(request: Request, scenario_name: str) -> None:
    """Emit notebook_updated event if event stream is configured."""
    settings = getattr(request.app.state, "app_settings", None)
    if settings is None:
        return
    event_path: Path = settings.event_stream_path
    if not event_path.parent.exists():
        return
    from autocontext.loop.events import EventStreamEmitter

    emitter = EventStreamEmitter(event_path)
    emitter.emit("notebook_updated", {"scenario_name": scenario_name}, channel="notebook")
