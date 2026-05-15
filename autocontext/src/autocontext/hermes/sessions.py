"""AC-706 slice 2: Hermes session DB domain types and read-only repository.

Hermes v0.12 stores sessions in a SQLite file at
``<home>/state.db``. The schema centers on:

* ``sessions``: top-level conversation envelopes (id, started_at,
  agent_id, free-form metadata),
* ``messages``: per-session message rows (session_id, seq, role,
  content, timestamp, metadata).

The repository here is the one place that talks to SQLite for the
ingest workflow. It opens the DB in read-only URI mode so any
accidental write attempt fails fast (AC-706 invariant: never mutate
the Hermes DB), and it tolerates schema drift by reading only the
columns it needs and treating optional columns as missing when absent.

The domain types (``HermesSession``, ``HermesMessage``) are frozen
dataclasses with slot storage. They carry only the fields the ingest
workflow uses; new Hermes columns are ignored, which is the
schema-drift posture AC-706 calls for.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class SessionDBMissing(FileNotFoundError):
    """Raised when the Hermes session DB does not exist.

    A dedicated subclass so the ingester can distinguish "no DB to
    ingest" (empty summary, exit 0) from "DB exists but is malformed"
    (sqlite3.DatabaseError, exit 1).
    """


@dataclass(frozen=True, slots=True)
class HermesSession:
    """A single Hermes session envelope."""

    session_id: str
    started_at: str | None
    ended_at: str | None
    agent_id: str | None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class HermesMessage:
    """A single message within a Hermes session."""

    session_id: str
    seq: int
    role: str
    content: str
    timestamp: str | None
    metadata: dict[str, Any] = field(default_factory=dict)


# Columns the repository expects. Anything outside this set is ignored
# (schema drift tolerance). Anything missing from the actual DB is
# treated as absent and yields None / {} on the domain object.
_SESSION_COLUMNS = ("session_id", "started_at", "ended_at", "agent_id", "metadata")
_MESSAGE_COLUMNS = ("session_id", "seq", "role", "content", "timestamp", "metadata")


class HermesSessionRepository:
    """Read-only access to a Hermes session DB.

    Opens via SQLite URI ``mode=ro`` so writes through the underlying
    connection raise ``sqlite3.OperationalError``. WAL/SHM sidecars
    are tolerated when absent (the connection opens read-only against
    the main DB file directly).
    """

    def __init__(self, db_path: Path) -> None:
        if not db_path.exists():
            raise SessionDBMissing(f"Hermes session DB not found: {db_path}")
        self._db_path = db_path
        uri = f"file:{db_path}?mode=ro"
        self._connection = sqlite3.connect(uri, uri=True)
        self._connection.row_factory = sqlite3.Row
        self._session_columns = self._existing_columns("sessions", _SESSION_COLUMNS)
        self._message_columns = self._existing_columns("messages", _MESSAGE_COLUMNS)

    @property
    def db_path(self) -> Path:
        return self._db_path

    def iter_sessions(self, *, since: datetime | None = None) -> Iterator[HermesSession]:
        """Yield :class:`HermesSession` objects ordered by ``started_at``.

        ``since`` filters out sessions whose ``started_at`` is strictly
        before the given datetime. Sessions with unparseable or missing
        ``started_at`` are passed through (the ingester can decide what
        to do with them).
        """
        if "sessions" not in self._table_names():
            return
        cols = self._session_columns
        col_sql = ", ".join(cols)
        cursor = self._connection.execute(f"SELECT {col_sql} FROM sessions ORDER BY started_at")
        for row in cursor:
            session = self._row_to_session(row, cols)
            if since is not None:
                started = _parse_iso(session.started_at)
                if started is not None and started < since:
                    continue
            yield session

    def iter_messages(self, session_id: str) -> Iterator[HermesMessage]:
        """Yield :class:`HermesMessage` rows for ``session_id`` in ``seq`` order."""
        if "messages" not in self._table_names():
            return
        cols = self._message_columns
        col_sql = ", ".join(cols)
        order_clause = "ORDER BY seq" if "seq" in cols else ""
        cursor = self._connection.execute(
            f"SELECT {col_sql} FROM messages WHERE session_id = ? {order_clause}",
            (session_id,),
        )
        for row in cursor:
            yield self._row_to_message(row, cols)

    # --- internals -----------------------------------------------------

    def _table_names(self) -> set[str]:
        cursor = self._connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        return {row[0] for row in cursor}

    def _existing_columns(self, table: str, expected: tuple[str, ...]) -> tuple[str, ...]:
        if table not in self._table_names():
            return ()
        cursor = self._connection.execute(f"PRAGMA table_info({table})")
        actual = {row[1] for row in cursor}
        return tuple(c for c in expected if c in actual)

    def _row_to_session(self, row: sqlite3.Row, cols: tuple[str, ...]) -> HermesSession:
        get = lambda c: row[c] if c in cols else None  # noqa: E731
        return HermesSession(
            session_id=str(get("session_id")),
            started_at=_as_str(get("started_at")),
            ended_at=_as_str(get("ended_at")),
            agent_id=_as_str(get("agent_id")),
            metadata=_parse_metadata(get("metadata")),
        )

    def _row_to_message(self, row: sqlite3.Row, cols: tuple[str, ...]) -> HermesMessage:
        get = lambda c: row[c] if c in cols else None  # noqa: E731
        return HermesMessage(
            session_id=str(get("session_id")),
            seq=int(get("seq") or 0),
            role=str(get("role") or ""),
            content=str(get("content") or ""),
            timestamp=_as_str(get("timestamp")),
            metadata=_parse_metadata(get("metadata")),
        )


def _as_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _parse_metadata(value: Any) -> dict[str, Any]:
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00") if value.endswith("Z") else value
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


__all__ = [
    "HermesMessage",
    "HermesSession",
    "HermesSessionRepository",
    "SessionDBMissing",
]
