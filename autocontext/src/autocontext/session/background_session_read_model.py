from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal, TypeAlias
from urllib.parse import quote

from autocontext.session.runtime_events import RuntimeSessionEventLog

BackgroundSessionStatus: TypeAlias = Literal["queued", "running", "completed", "failed", "canceled", "skipped", "unknown"]
BackgroundSessionSummary: TypeAlias = dict[str, str | int]
BackgroundSessionArtifact: TypeAlias = dict[str, str]
BackgroundSessionDetail: TypeAlias = dict[str, Any]
TaskQueueRowLike: TypeAlias = Mapping[str, Any]
RunRowLike: TypeAlias = Mapping[str, Any]
ArtifactLike: TypeAlias = Mapping[str, Any]


def build_background_session_summary(
    *,
    runtime_session: RuntimeSessionEventLog | None = None,
    task: TaskQueueRowLike | None = None,
    run: RunRowLike | None = None,
    artifacts: Sequence[ArtifactLike] | None = None,
    child_sessions: Sequence[RuntimeSessionEventLog] | None = None,
) -> BackgroundSessionSummary:
    artifacts = artifacts or []
    child_sessions = child_sessions or []
    session_id = runtime_session.session_id if runtime_session else _task_session_id(task)
    created_at = (
        runtime_session.created_at if runtime_session else _task_str(task, "created_at") or _run_str(run, "created_at")
    )
    updated_at = _updated_at(runtime_session, task, run)
    status = _normalize_status(
        _metadata_str(runtime_session, "status") or _task_str(task, "status") or _run_str(run, "status"),
        has_runtime_session=runtime_session is not None,
    )

    return {
        "session_id": session_id,
        "runtime_session_id": runtime_session.session_id if runtime_session else "",
        "run_id": _metadata_str(runtime_session, "runId") or _run_str(run, "run_id"),
        "task_id": runtime_session.task_id if runtime_session and runtime_session.task_id else _task_str(task, "id"),
        "parent_session_id": runtime_session.parent_session_id if runtime_session else "",
        "status": status,
        "goal": _metadata_str(runtime_session, "goal") or _task_str(task, "spec_name") or _run_str(run, "scenario"),
        "event_count": len(runtime_session.events) if runtime_session else 0,
        "artifact_count": len(artifacts),
        "child_session_count": len(child_sessions),
        "created_at": created_at,
        "updated_at": updated_at,
        "result_url": background_session_url(session_id),
        "runtime_session_url": runtime_session_url(runtime_session.session_id if runtime_session else ""),
    }


def build_background_session_detail(
    *,
    runtime_session: RuntimeSessionEventLog | None = None,
    task: TaskQueueRowLike | None = None,
    run: RunRowLike | None = None,
    artifacts: Sequence[ArtifactLike] | None = None,
    child_sessions: Sequence[RuntimeSessionEventLog] | None = None,
) -> BackgroundSessionDetail:
    return {
        "summary": build_background_session_summary(
            runtime_session=runtime_session,
            task=task,
            run=run,
            artifacts=artifacts,
            child_sessions=child_sessions,
        ),
        "artifacts": [_sanitize_artifact(artifact) for artifact in (artifacts or [])],
        "child_sessions": [build_background_session_summary(runtime_session=child) for child in (child_sessions or [])],
        "trigger": _read_trigger(task),
    }


def background_session_url(session_id: str) -> str:
    return f"/api/cockpit/background-sessions/{quote(session_id, safe='')}" if session_id else ""


def runtime_session_url(session_id: str) -> str:
    return f"/api/cockpit/runtime-sessions/{quote(session_id, safe='')}" if session_id else ""


def _sanitize_artifact(artifact: ArtifactLike) -> BackgroundSessionArtifact:
    return {
        "artifact_id": _read_str(artifact.get("artifact_id")),
        "kind": _read_str(artifact.get("kind")) or "file",
        "label": _read_str(artifact.get("label")),
        "path": _read_str(artifact.get("path")),
        "url": _read_str(artifact.get("url")),
    }


def _read_trigger(task: TaskQueueRowLike | None) -> dict[str, str | int | bool] | None:
    raw = _task_str(task, "config_json")
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    trigger = parsed.get("trigger")
    if not isinstance(trigger, dict):
        return None
    return _sanitize_record(trigger)


def _sanitize_record(record: Mapping[str, Any]) -> dict[str, str | int | bool]:
    clean: dict[str, str | int | bool] = {}
    for key, value in record.items():
        if isinstance(key, str) and isinstance(value, str | int | bool):
            clean[key] = value
    return clean


def _normalize_status(raw: str, *, has_runtime_session: bool) -> BackgroundSessionStatus:
    status = raw.strip().lower().replace("-", "_")
    if status in {"pending", "queued", "scheduled", "backlog"}:
        return "queued"
    if status in {"running", "started", "in_progress", "processing"}:
        return "running"
    if status in {"completed", "complete", "done", "success", "succeeded"}:
        return "completed"
    if status in {"failed", "failure", "error"}:
        return "failed"
    if status in {"canceled", "cancelled"}:
        return "canceled"
    if status == "skipped":
        return "skipped"
    return "running" if has_runtime_session else "unknown"


def _updated_at(
    runtime_session: RuntimeSessionEventLog | None,
    task: TaskQueueRowLike | None,
    run: RunRowLike | None,
) -> str:
    if runtime_session:
        return runtime_session.updated_at or runtime_session.created_at
    return (
        _task_str(task, "updated_at")
        or _task_str(task, "created_at")
        or _run_str(run, "updated_at")
        or _run_str(run, "created_at")
    )


def _task_session_id(task: TaskQueueRowLike | None) -> str:
    task_id = _task_str(task, "id")
    return f"task:{task_id}" if task_id else ""


def _metadata_str(runtime_session: RuntimeSessionEventLog | None, key: str) -> str:
    value = runtime_session.metadata.get(key) if runtime_session else None
    return value if isinstance(value, str) else ""


def _task_str(task: TaskQueueRowLike | None, key: str) -> str:
    value = task.get(key) if task else None
    return value if isinstance(value, str) else ""


def _run_str(run: RunRowLike | None, key: str) -> str:
    value = run.get(key) if run else None
    return value if isinstance(value, str) else ""


def _read_str(value: Any) -> str:
    return value if isinstance(value, str) else ""
