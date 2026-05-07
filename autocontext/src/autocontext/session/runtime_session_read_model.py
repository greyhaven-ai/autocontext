from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from autocontext.session.runtime_events import RuntimeSessionEventLog
from autocontext.session.runtime_session_ids import runtime_session_id_for_run

RuntimeSessionSummary = dict[str, str | int]


class RuntimeSessionReadStore(Protocol):
    def list(self, *, limit: int = 50) -> list[RuntimeSessionEventLog]: ...

    def load(self, session_id: str) -> RuntimeSessionEventLog | None: ...


def summarize_runtime_session(log: RuntimeSessionEventLog) -> RuntimeSessionSummary:
    return {
        "session_id": log.session_id,
        "parent_session_id": log.parent_session_id,
        "task_id": log.task_id,
        "worker_id": log.worker_id,
        "goal": _metadata_str(log.metadata, "goal"),
        "event_count": len(log.events),
        "created_at": log.created_at,
        "updated_at": log.updated_at or log.created_at,
    }


def read_runtime_session_summaries(
    store: RuntimeSessionReadStore | Mapping[str, RuntimeSessionEventLog],
    *,
    limit: int = 50,
) -> list[RuntimeSessionSummary]:
    return [summarize_runtime_session(log) for log in _list_logs(store, limit=limit)]


def read_runtime_session_by_id(
    store: RuntimeSessionReadStore | Mapping[str, RuntimeSessionEventLog],
    session_id: str,
) -> RuntimeSessionEventLog | None:
    if isinstance(store, Mapping):
        return store.get(session_id)
    return store.load(session_id)


def read_runtime_session_by_run_id(
    store: RuntimeSessionReadStore | Mapping[str, RuntimeSessionEventLog],
    run_id: str,
) -> RuntimeSessionEventLog | None:
    return read_runtime_session_by_id(store, runtime_session_id_for_run(run_id))


def _list_logs(
    store: RuntimeSessionReadStore | Mapping[str, RuntimeSessionEventLog],
    *,
    limit: int,
) -> list[RuntimeSessionEventLog]:
    if isinstance(store, Mapping):
        return list(store.values())[:limit]
    return store.list(limit=limit)


def _metadata_str(metadata: dict[str, Any], key: str) -> str:
    value = metadata.get(key)
    return value if isinstance(value, str) else ""
