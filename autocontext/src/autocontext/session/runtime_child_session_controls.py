from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from autocontext.session.runtime_events import (
    RuntimeSessionEvent,
    RuntimeSessionEventLog,
    RuntimeSessionEventStore,
    RuntimeSessionEventType,
)

DEFAULT_CHILD_TASK_MAX_CONCURRENT = 8


@dataclass(frozen=True)
class RuntimeChildSessionCancellation:
    child_session_id: str
    parent_session_id: str
    task_id: str
    worker_id: str
    status: str
    reason: str
    child_session_log: RuntimeSessionEventLog


def observe_runtime_session_log(
    log: RuntimeSessionEventLog,
    event_store: RuntimeSessionEventStore | None,
    event_sink: Any,
) -> None:
    if event_store is None and event_sink is None:
        return

    def on_event(event: RuntimeSessionEvent, current_log: RuntimeSessionEventLog) -> None:
        if event_store is not None:
            event_store.save(current_log)
        if event_sink is not None:
            try:
                event_sink.on_runtime_session_event(event, current_log)
            except Exception:
                pass

    log.subscribe(on_event)


def json_safe_record(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    return {str(key): _json_safe_value(item) for key, item in value.items()}


def _json_safe_value(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError):
        if isinstance(value, Mapping):
            return {str(key): _json_safe_value(item) for key, item in value.items()}
        if isinstance(value, list | tuple):
            return [_json_safe_value(item) for item in value]
        return str(value)


def normalize_depth(value: int, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        msg = f"{name} must be a non-negative integer"
        raise ValueError(msg)
    return value


def normalize_positive_int(value: int, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        msg = f"{name} must be a positive integer"
        raise ValueError(msg)
    return value


def child_task_coordinator_lineage(
    *,
    task_id: str,
    child_session_id: str,
    parent_session_id: str,
    role: str,
    cwd: str,
    depth: int,
    max_depth: int,
) -> dict[str, Any]:
    return {
        "taskId": task_id,
        "childSessionId": child_session_id,
        "parentSessionId": parent_session_id,
        "role": role,
        "cwd": cwd,
        "depth": depth,
        "maxDepth": max_depth,
    }


def active_child_session_ids(log: RuntimeSessionEventLog) -> set[str]:
    active: set[str] = set()
    for event in log.events:
        if event.event_type == RuntimeSessionEventType.CHILD_TASK_STARTED:
            child_session_id = _read_str(event.payload.get("childSessionId"))
            if child_session_id:
                active.add(child_session_id)
        elif event.event_type == RuntimeSessionEventType.CHILD_TASK_COMPLETED:
            child_session_id = _read_str(event.payload.get("childSessionId"))
            if child_session_id:
                active.discard(child_session_id)
    return active


def cancel_child_session_for_parent(
    *,
    parent_log: RuntimeSessionEventLog,
    event_store: RuntimeSessionEventStore,
    child_session_id: str,
    reason: str,
) -> RuntimeChildSessionCancellation:
    clean_child_session_id = child_session_id.strip()
    if not clean_child_session_id:
        msg = "child_session_id is required"
        raise ValueError(msg)
    child_log = event_store.load(clean_child_session_id)
    if child_log is None:
        raise KeyError(f"Child session '{clean_child_session_id}' not found")
    if child_log.parent_session_id != parent_log.session_id:
        msg = f"Child session '{clean_child_session_id}' does not belong to parent '{parent_log.session_id}'"
        raise ValueError(msg)
    clean_reason = reason.strip() or "canceled"
    child_log.metadata["status"] = "canceled"
    child_log.append(
        RuntimeSessionEventType.ASSISTANT_MESSAGE,
        {"text": "", "error": clean_reason, "isError": True, "phase": "canceled", "status": "canceled"},
    )
    parent_log.append(
        RuntimeSessionEventType.CHILD_TASK_COMPLETED,
        {
            "taskId": child_log.task_id,
            "childSessionId": child_log.session_id,
            "workerId": child_log.worker_id,
            "result": "",
            "error": clean_reason,
            "isError": True,
            "phase": "canceled",
            "status": "canceled",
        },
    )
    event_store.save(parent_log)
    event_store.save(child_log)
    return RuntimeChildSessionCancellation(
        child_session_id=child_log.session_id,
        parent_session_id=parent_log.session_id,
        task_id=child_log.task_id,
        worker_id=child_log.worker_id,
        status="canceled",
        reason=clean_reason,
        child_session_log=child_log,
    )


def load_canceled_child_log(
    event_store: RuntimeSessionEventStore | None,
    child_log: RuntimeSessionEventLog,
) -> RuntimeSessionEventLog | None:
    persisted = event_store.load(child_log.session_id) if event_store is not None else None
    for candidate in (persisted, child_log):
        if candidate is not None and is_canceled_child_log(candidate):
            return candidate
    return None


def is_canceled_child_log(log: RuntimeSessionEventLog) -> bool:
    if _is_canceled_value(log.metadata.get("status")):
        return True
    return any(
        event.event_type == RuntimeSessionEventType.ASSISTANT_MESSAGE
        and (_is_canceled_value(event.payload.get("phase")) or _is_canceled_value(event.payload.get("status")))
        for event in log.events
    )


def canceled_child_reason(log: RuntimeSessionEventLog) -> str:
    for event in reversed(log.events):
        if event.event_type != RuntimeSessionEventType.ASSISTANT_MESSAGE:
            continue
        if not (_is_canceled_value(event.payload.get("phase")) or _is_canceled_value(event.payload.get("status"))):
            continue
        return _read_str(event.payload.get("error")) or "canceled"
    return "canceled"


def _is_canceled_value(value: Any) -> bool:
    normalized = _read_str(value).lower().replace("-", "_")
    return normalized in {"canceled", "cancelled"}


def _read_str(value: Any) -> str:
    return value if isinstance(value, str) else ""
