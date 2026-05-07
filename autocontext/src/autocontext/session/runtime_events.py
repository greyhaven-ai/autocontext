"""Runtime-session event logs for provider-backed runs.

This is intentionally separate from the older Session aggregate in
``autocontext.session.types``. Runtime sessions are append-only observability
logs for runtime prompts, assistant messages, shell/tool calls, and child-task
lineage; they mirror the TypeScript runtime-session JSON shape so Python and
TypeScript readers can share stored logs.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, Field, PrivateAttr


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class RuntimeSessionEventType(StrEnum):
    """Runtime-session event kinds shared with the TypeScript package."""

    PROMPT_SUBMITTED = "prompt_submitted"
    ASSISTANT_MESSAGE = "assistant_message"
    SHELL_COMMAND = "shell_command"
    TOOL_CALL = "tool_call"
    CHILD_TASK_STARTED = "child_task_started"
    CHILD_TASK_COMPLETED = "child_task_completed"
    COMPACTION = "compaction"


class RuntimeSessionEvent(BaseModel):
    """A single immutable runtime-session event."""

    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    session_id: str
    sequence: int
    event_type: RuntimeSessionEventType
    timestamp: str = Field(default_factory=_now_iso)
    payload: dict[str, Any] = Field(default_factory=dict)
    parent_session_id: str = ""
    task_id: str = ""
    worker_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return the TypeScript-compatible camelCase JSON shape."""
        return {
            "eventId": self.event_id,
            "sessionId": self.session_id,
            "sequence": self.sequence,
            "eventType": self.event_type.value,
            "timestamp": self.timestamp,
            "payload": dict(self.payload),
            "parentSessionId": self.parent_session_id,
            "taskId": self.task_id,
            "workerId": self.worker_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Parse either the TypeScript camelCase or Python snake_case shape."""
        return cls(
            event_id=_read_str(data.get("eventId", data.get("event_id"))) or uuid.uuid4().hex[:12],
            session_id=_read_str(data.get("sessionId", data.get("session_id"))),
            sequence=_read_int(data.get("sequence")),
            event_type=_read_event_type(data.get("eventType", data.get("event_type"))),
            timestamp=_read_str(data.get("timestamp")) or _now_iso(),
            payload=_read_record(data.get("payload")),
            parent_session_id=_read_str(data.get("parentSessionId", data.get("parent_session_id"))),
            task_id=_read_str(data.get("taskId", data.get("task_id"))),
            worker_id=_read_str(data.get("workerId", data.get("worker_id"))),
        )


RuntimeSessionEventLogSubscriber = Callable[[RuntimeSessionEvent, "RuntimeSessionEventLog"], None]


class RuntimeSessionEventLog(BaseModel):
    """Append-only event log for one runtime session."""

    session_id: str
    parent_session_id: str = ""
    task_id: str = ""
    worker_id: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    events: list[RuntimeSessionEvent] = Field(default_factory=list)
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = ""

    _subscribers: list[RuntimeSessionEventLogSubscriber] = PrivateAttr(default_factory=list)

    @classmethod
    def create(
        cls,
        *,
        session_id: str,
        parent_session_id: str = "",
        task_id: str = "",
        worker_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> Self:
        return cls(
            session_id=session_id,
            parent_session_id=parent_session_id,
            task_id=task_id,
            worker_id=worker_id,
            metadata=metadata or {},
        )

    def append(
        self,
        event_type: RuntimeSessionEventType,
        payload: dict[str, Any] | None = None,
    ) -> RuntimeSessionEvent:
        event = RuntimeSessionEvent(
            session_id=self.session_id,
            sequence=len(self.events),
            event_type=event_type,
            payload=payload or {},
            parent_session_id=self.parent_session_id,
            task_id=self.task_id,
            worker_id=self.worker_id,
        )
        self.events.append(event)
        self.updated_at = event.timestamp
        self._notify(event)
        return event

    def subscribe(self, callback: RuntimeSessionEventLogSubscriber) -> Callable[[], None]:
        self._subscribers.append(callback)

        def unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return unsubscribe

    def to_dict(self) -> dict[str, Any]:
        return {
            "sessionId": self.session_id,
            "parentSessionId": self.parent_session_id,
            "taskId": self.task_id,
            "workerId": self.worker_id,
            "metadata": dict(self.metadata),
            "events": [event.to_dict() for event in self.events],
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        events = data.get("events")
        event_records = events if isinstance(events, list) else []
        return cls(
            session_id=_read_str(data.get("sessionId", data.get("session_id"))),
            parent_session_id=_read_str(data.get("parentSessionId", data.get("parent_session_id"))),
            task_id=_read_str(data.get("taskId", data.get("task_id"))),
            worker_id=_read_str(data.get("workerId", data.get("worker_id"))),
            metadata=_read_record(data.get("metadata")),
            events=[
                RuntimeSessionEvent.from_dict(event)
                for event in event_records
                if isinstance(event, dict)
            ],
            created_at=_read_str(data.get("createdAt", data.get("created_at"))) or _now_iso(),
            updated_at=_read_str(data.get("updatedAt", data.get("updated_at"))),
        )

    def _notify(self, event: RuntimeSessionEvent) -> None:
        for subscriber in list(self._subscribers):
            subscriber(event, self)


RuntimeSessionEventLogList = list[RuntimeSessionEventLog]


class RuntimeSessionEventStore:
    """SQLite-backed store for runtime-session event logs."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def save(self, log: RuntimeSessionEventLog) -> None:
        data = log.to_dict()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO runtime_sessions (
                    session_id, parent_session_id, task_id, worker_id, metadata_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    parent_session_id = excluded.parent_session_id,
                    task_id = excluded.task_id,
                    worker_id = excluded.worker_id,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (
                    data["sessionId"],
                    data["parentSessionId"],
                    data["taskId"],
                    data["workerId"],
                    json.dumps(data["metadata"]),
                    data["createdAt"],
                    data["updatedAt"],
                ),
            )
            conn.execute("DELETE FROM runtime_session_events WHERE session_id = ?", (data["sessionId"],))
            conn.executemany(
                """
                INSERT INTO runtime_session_events (
                    event_id, session_id, sequence, event_type, timestamp,
                    parent_session_id, task_id, worker_id, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        event["eventId"],
                        event["sessionId"],
                        event["sequence"],
                        event["eventType"],
                        event["timestamp"],
                        event["parentSessionId"],
                        event["taskId"],
                        event["workerId"],
                        json.dumps(event["payload"]),
                    )
                    for event in data["events"]
                ],
            )

    def load(self, session_id: str) -> RuntimeSessionEventLog | None:
        with self._connection() as conn:
            session = conn.execute(
                """
                SELECT session_id, parent_session_id, task_id, worker_id, metadata_json, created_at, updated_at
                FROM runtime_sessions
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            if session is None:
                return None
            events = conn.execute(
                """
                SELECT event_id, session_id, sequence, event_type, timestamp,
                       parent_session_id, task_id, worker_id, payload_json
                FROM runtime_session_events
                WHERE session_id = ?
                ORDER BY sequence ASC
                """,
                (session_id,),
            ).fetchall()
        return RuntimeSessionEventLog.from_dict(
            {
                "sessionId": session["session_id"],
                "parentSessionId": session["parent_session_id"],
                "taskId": session["task_id"],
                "workerId": session["worker_id"],
                "metadata": _safe_json_record(session["metadata_json"]),
                "createdAt": session["created_at"],
                "updatedAt": session["updated_at"],
                "events": [
                    {
                        "eventId": event["event_id"],
                        "sessionId": event["session_id"],
                        "sequence": event["sequence"],
                        "eventType": event["event_type"],
                        "timestamp": event["timestamp"],
                        "parentSessionId": event["parent_session_id"],
                        "taskId": event["task_id"],
                        "workerId": event["worker_id"],
                        "payload": _safe_json_record(event["payload_json"]),
                    }
                    for event in events
                ],
            }
        )

    def list(self, *, limit: int = 50) -> RuntimeSessionEventLogList:
        clean_limit = limit if isinstance(limit, int) and limit > 0 else 50
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT session_id
                FROM runtime_sessions
                ORDER BY COALESCE(NULLIF(updated_at, ''), created_at) DESC, created_at DESC, session_id ASC
                LIMIT ?
                """,
                (clean_limit,),
            ).fetchall()
        return [log for row in rows if (log := self.load(row["session_id"])) is not None]

    def list_children(self, parent_session_id: str) -> RuntimeSessionEventLogList:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT session_id
                FROM runtime_sessions
                WHERE parent_session_id = ?
                ORDER BY created_at ASC, session_id ASC
                """,
                (parent_session_id,),
            ).fetchall()
        return [log for row in rows if (log := self.load(row["session_id"])) is not None]

    def close(self) -> None:
        """Compatibility no-op; operation-scoped connections close immediately."""

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with closing(self._connect()) as conn:
            with conn:
                yield conn

    def _ensure_schema(self) -> None:
        with self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS runtime_sessions (
                    session_id TEXT PRIMARY KEY,
                    parent_session_id TEXT NOT NULL DEFAULT '',
                    task_id TEXT NOT NULL DEFAULT '',
                    worker_id TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS runtime_session_events (
                    event_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    parent_session_id TEXT NOT NULL DEFAULT '',
                    task_id TEXT NOT NULL DEFAULT '',
                    worker_id TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL,
                    UNIQUE(session_id, sequence)
                );

                CREATE INDEX IF NOT EXISTS idx_runtime_sessions_parent
                ON runtime_sessions(parent_session_id);

                CREATE INDEX IF NOT EXISTS idx_runtime_sessions_updated
                ON runtime_sessions(updated_at, created_at);

                CREATE INDEX IF NOT EXISTS idx_runtime_session_events_session
                ON runtime_session_events(session_id, sequence);
                """
            )


def _read_record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _read_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _read_int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _read_event_type(value: Any) -> RuntimeSessionEventType:
    if isinstance(value, RuntimeSessionEventType):
        return value
    return RuntimeSessionEventType(str(value))


def _safe_json_record(raw: str) -> dict[str, Any]:
    try:
        return _read_record(json.loads(raw))
    except json.JSONDecodeError:
        return {}
