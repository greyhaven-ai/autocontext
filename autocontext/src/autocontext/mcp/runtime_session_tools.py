from __future__ import annotations

from typing import Any

from autocontext.mcp._base import MtsToolContext
from autocontext.session.runtime_events import RuntimeSessionEventStore
from autocontext.session.runtime_session_ids import runtime_session_id_for_run
from autocontext.session.runtime_session_read_model import (
    read_runtime_session_by_id,
    read_runtime_session_by_run_id,
    read_runtime_session_summaries,
)
from autocontext.session.runtime_session_timeline import (
    read_runtime_session_timeline_by_id,
    read_runtime_session_timeline_by_run_id,
)


def list_runtime_sessions(ctx: MtsToolContext, limit: int = 50) -> dict[str, Any]:
    """List recent runtime-session event logs."""
    store = _event_store(ctx)
    try:
        return {"sessions": read_runtime_session_summaries(store, limit=limit)}
    finally:
        store.close()


def get_runtime_session(
    ctx: MtsToolContext,
    *,
    session_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Read a runtime-session event log by session id or run id."""
    resolved = _resolve_runtime_session_identifier(session_id=session_id, run_id=run_id)
    if resolved["error"]:
        return {"error": resolved["error"]}

    store = _event_store(ctx)
    try:
        log = (
            read_runtime_session_by_run_id(store, resolved["run_id"])
            if resolved["run_id"]
            else read_runtime_session_by_id(store, resolved["session_id"])
        )
        if log is None:
            return {"error": "Runtime session not found", "session_id": resolved["session_id"]}
        return log.to_dict()
    finally:
        store.close()


def get_runtime_session_timeline(
    ctx: MtsToolContext,
    *,
    session_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Read an operator-facing runtime-session timeline by session id or run id."""
    resolved = _resolve_runtime_session_identifier(session_id=session_id, run_id=run_id)
    if resolved["error"]:
        return {"error": resolved["error"]}

    store = _event_store(ctx)
    try:
        timeline = (
            read_runtime_session_timeline_by_run_id(store, resolved["run_id"])
            if resolved["run_id"]
            else read_runtime_session_timeline_by_id(store, resolved["session_id"])
        )
        if timeline is None:
            return {"error": "Runtime session not found", "session_id": resolved["session_id"]}
        return timeline
    finally:
        store.close()


def _event_store(ctx: MtsToolContext) -> RuntimeSessionEventStore:
    return RuntimeSessionEventStore(ctx.settings.db_path)


def _resolve_runtime_session_identifier(
    *,
    session_id: str | None,
    run_id: str | None,
) -> dict[str, str]:
    clean_session_id = (session_id or "").strip()
    clean_run_id = (run_id or "").strip()
    if bool(clean_session_id) == bool(clean_run_id):
        return {
            "error": "Provide exactly one of session_id or run_id",
            "session_id": "",
            "run_id": "",
        }
    return {
        "error": "",
        "session_id": clean_session_id or runtime_session_id_for_run(clean_run_id),
        "run_id": clean_run_id,
    }
