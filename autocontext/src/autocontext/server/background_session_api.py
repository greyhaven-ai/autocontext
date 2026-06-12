from __future__ import annotations

import json
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request

from autocontext.session.background_session_events import normalize_background_session_timeline
from autocontext.session.background_session_read_model import build_background_session_detail, build_background_session_summary
from autocontext.session.runtime_events import RuntimeSessionEventLog, RuntimeSessionEventStore
from autocontext.session.runtime_session_read_model import read_runtime_session_by_id
from autocontext.storage import SQLiteStore

background_session_router = APIRouter(prefix="/background-sessions", tags=["cockpit"])


def _get_store(request: Request) -> SQLiteStore:
    store = getattr(request.app.state, "store", None)
    if not isinstance(store, SQLiteStore):
        raise HTTPException(status_code=500, detail="Application store is not configured")
    return store


def _get_runtime_session_store(request: Request) -> RuntimeSessionEventStore:
    settings = getattr(request.app.state, "app_settings", None)
    if settings is None:
        raise HTTPException(status_code=500, detail="Application settings are not configured")
    return RuntimeSessionEventStore(settings.db_path)


def _background_session_not_found(message: str, session_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail={"detail": message, "session_id": session_id})


def _background_session_summary(
    runtime_store: RuntimeSessionEventStore,
    runtime_session: RuntimeSessionEventLog,
    store: SQLiteStore,
    task_index: dict[str, dict[str, Any]] | None = None,
) -> dict[str, str | int]:
    task = _task_for_runtime_session(store, runtime_session, task_index)
    return cast(
        dict[str, str | int],
        build_background_session_summary(
            runtime_session=runtime_session,
            task=task,
            run=_run_for_runtime_session(store, runtime_session, task),
            child_sessions=runtime_store.list_children(runtime_session.session_id),
        ),
    )


def _task_for_runtime_session(
    store: SQLiteStore,
    runtime_session: RuntimeSessionEventLog,
    task_index: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    if not runtime_session.task_id:
        return None
    if task_index is not None and runtime_session.task_id in task_index:
        return task_index[runtime_session.task_id]
    return store.get_task(runtime_session.task_id)


def _run_for_runtime_session(
    store: SQLiteStore,
    runtime_session: RuntimeSessionEventLog,
    task: dict[str, Any] | None,
) -> dict[str, Any] | None:
    run_id = _read_str(runtime_session.metadata.get("runId")) or _run_id_from_task(task)
    return store.get_run(run_id) if run_id else None


def _run_id_from_task(task: dict[str, Any] | None) -> str:
    if not task:
        return ""
    config_json = task.get("config_json")
    if not isinstance(config_json, str) or not config_json:
        return ""
    try:
        parsed = json.loads(config_json)
    except json.JSONDecodeError:
        return ""
    if not isinstance(parsed, dict):
        return ""
    return _read_str(parsed.get("run_id")) or _read_str(parsed.get("runId"))


def _read_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _task_id_from_background_session_id(session_id: str) -> str:
    return session_id.removeprefix("task:") if session_id.startswith("task:") else ""


def _sort_background_session_summaries(summaries: list[dict[str, str | int]]) -> list[dict[str, str | int]]:
    ordered = list(summaries)
    ordered.sort(key=lambda summary: str(summary.get("session_id", "")))
    ordered.sort(key=lambda summary: str(summary.get("created_at", "")), reverse=True)
    ordered.sort(
        key=lambda summary: str(summary.get("updated_at") or summary.get("created_at") or ""),
        reverse=True,
    )
    return ordered


@background_session_router.get("")
def list_background_sessions(request: Request, limit: int = 50) -> dict[str, Any]:
    """List operator-facing background-session summaries."""
    if limit <= 0:
        raise HTTPException(status_code=422, detail="limit must be a positive integer")
    store = _get_store(request)
    runtime_store = _get_runtime_session_store(request)
    try:
        runtime_sessions = runtime_store.list(limit=limit)
        tasks = store.list_tasks(limit=limit)
        task_index = {str(task["id"]): task for task in tasks if isinstance(task.get("id"), str)}
        runtime_task_ids = {runtime_session.task_id for runtime_session in runtime_sessions if runtime_session.task_id}
        summaries = [
            _background_session_summary(runtime_store, runtime_session, store, task_index) for runtime_session in runtime_sessions
        ]
        summaries.extend(
            build_background_session_summary(task=task)
            for task in tasks
            if isinstance(task.get("id"), str) and task["id"] not in runtime_task_ids
        )
        return {"sessions": _sort_background_session_summaries(summaries)[:limit]}
    finally:
        runtime_store.close()


@background_session_router.get("/{session_id}")
def get_background_session(session_id: str, request: Request) -> dict[str, Any]:
    """Read an operator-facing background-session detail by session id."""
    clean_session_id = session_id.strip()
    if not clean_session_id:
        raise HTTPException(status_code=422, detail="session_id is required")
    store = _get_store(request)
    runtime_store = _get_runtime_session_store(request)
    try:
        log = read_runtime_session_by_id(runtime_store, clean_session_id)
        if log is not None:
            task = _task_for_runtime_session(store, log)
            child_sessions = runtime_store.list_children(log.session_id)
            return {
                **build_background_session_detail(
                    runtime_session=log,
                    task=task,
                    run=_run_for_runtime_session(store, log, task),
                    child_sessions=child_sessions,
                ),
                "normalized_events": normalize_background_session_timeline(log, child_logs=child_sessions),
            }
        task_id = _task_id_from_background_session_id(clean_session_id)
        task = store.get_task(task_id) if task_id else None
        if task is not None:
            return {**build_background_session_detail(task=task), "normalized_events": []}
        raise _background_session_not_found(
            f"Background session '{clean_session_id}' not found",
            clean_session_id,
        )
    finally:
        runtime_store.close()
