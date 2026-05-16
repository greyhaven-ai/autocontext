"""AC-706 slice 2: read-only Hermes session DB repository.

Domain tests covering:

* read-only URI access (writes are refused),
* missing DB file produces a clear error,
* schema drift tolerance: missing optional columns do not raise,
* WAL/SHM sidecars are tolerated when absent and ignored when present,
* `iter_sessions` filters by ``started_at``,
* `iter_messages` returns rows in insertion order.

The repository is the only place that talks to SQLite directly; the
ingester layer (slice 2 application service) consumes the domain
types it yields. Keeping the two split lets us swap storage without
touching the ingest workflow.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from autocontext.hermes.sessions import (
    HermesMessage,
    HermesSession,
    HermesSessionRepository,
    SessionDBMissing,
)


def _plant_session_db(
    path: Path,
    *,
    sessions: list[dict],
    messages: list[dict],
    extra_columns: dict[str, list[str]] | None = None,
) -> None:
    """Create a SQLite DB shaped like a Hermes v0.12 session store.

    ``extra_columns`` lets each test simulate schema drift by adding
    columns the repository should ignore.
    """
    extra = extra_columns or {}
    session_extras = ", ".join(f"{c} TEXT" for c in extra.get("sessions", []))
    message_extras = ", ".join(f"{c} TEXT" for c in extra.get("messages", []))

    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE sessions ("
            "session_id TEXT PRIMARY KEY, "
            "started_at TEXT, "
            "ended_at TEXT, "
            "agent_id TEXT, "
            "metadata TEXT" + (f", {session_extras}" if session_extras else "") + ")"
        )
        conn.execute(
            "CREATE TABLE messages ("
            "session_id TEXT, "
            "seq INTEGER, "
            "role TEXT, "
            "content TEXT, "
            "timestamp TEXT, "
            "metadata TEXT" + (f", {message_extras}" if message_extras else "") + ")"
        )
        for s in sessions:
            cols = ["session_id", "started_at", "ended_at", "agent_id", "metadata"]
            extra_cols = list(extra.get("sessions", []))
            all_cols = cols + extra_cols
            placeholders = ",".join("?" for _ in all_cols)
            values = [s.get(c) for c in all_cols]
            conn.execute(
                f"INSERT INTO sessions ({','.join(all_cols)}) VALUES ({placeholders})",
                values,
            )
        for m in messages:
            cols = ["session_id", "seq", "role", "content", "timestamp", "metadata"]
            extra_cols = list(extra.get("messages", []))
            all_cols = cols + extra_cols
            placeholders = ",".join("?" for _ in all_cols)
            values = [m.get(c) for c in all_cols]
            conn.execute(
                f"INSERT INTO messages ({','.join(all_cols)}) VALUES ({placeholders})",
                values,
            )
        conn.commit()
    finally:
        conn.close()


def test_missing_db_file_raises_session_db_missing(tmp_path: Path) -> None:
    """A clear domain error beats a raw sqlite3.OperationalError so the
    ingester can decide between "empty summary" and "abort"."""
    with pytest.raises(SessionDBMissing, match="not found"):
        HermesSessionRepository(tmp_path / "state.db")


def test_iter_sessions_returns_domain_objects(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    _plant_session_db(
        db,
        sessions=[
            {
                "session_id": "s1",
                "started_at": "2026-05-01T10:00:00Z",
                "ended_at": "2026-05-01T10:30:00Z",
                "agent_id": "claude",
                "metadata": '{"topic":"billing"}',
            }
        ],
        messages=[],
    )
    repo = HermesSessionRepository(db)
    sessions = list(repo.iter_sessions())
    assert len(sessions) == 1
    s = sessions[0]
    assert isinstance(s, HermesSession)
    assert s.session_id == "s1"
    assert s.started_at == "2026-05-01T10:00:00Z"
    assert s.agent_id == "claude"
    assert s.metadata == {"topic": "billing"}


def test_iter_sessions_since_filter_drops_older_sessions(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    _plant_session_db(
        db,
        sessions=[
            {"session_id": "old", "started_at": "2026-04-01T00:00:00Z"},
            {"session_id": "new", "started_at": "2026-05-10T00:00:00Z"},
        ],
        messages=[],
    )
    repo = HermesSessionRepository(db)
    since = datetime.fromisoformat("2026-05-01T00:00:00+00:00")
    ids = [s.session_id for s in repo.iter_sessions(since=since)]
    assert ids == ["new"]


def test_iter_messages_returns_rows_in_seq_order(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    _plant_session_db(
        db,
        sessions=[{"session_id": "s1", "started_at": "2026-05-10T00:00:00Z"}],
        messages=[
            {"session_id": "s1", "seq": 2, "role": "assistant", "content": "second"},
            {"session_id": "s1", "seq": 1, "role": "user", "content": "first"},
            {"session_id": "s1", "seq": 3, "role": "user", "content": "third"},
        ],
    )
    repo = HermesSessionRepository(db)
    messages = list(repo.iter_messages("s1"))
    assert [m.content for m in messages] == ["first", "second", "third"]
    assert all(isinstance(m, HermesMessage) for m in messages)


def test_schema_drift_extra_columns_are_ignored(tmp_path: Path) -> None:
    """Hermes may add columns over time. The repository must keep
    working without code changes — it only reads the columns it
    needs."""
    db = tmp_path / "state.db"
    _plant_session_db(
        db,
        sessions=[
            {
                "session_id": "s1",
                "started_at": "2026-05-10T00:00:00Z",
                "future_field": "ignored",
            }
        ],
        messages=[{"session_id": "s1", "seq": 1, "role": "user", "content": "hi", "experimental": "x"}],
        extra_columns={"sessions": ["future_field"], "messages": ["experimental"]},
    )
    repo = HermesSessionRepository(db)
    sessions = list(repo.iter_sessions())
    assert sessions[0].session_id == "s1"
    messages = list(repo.iter_messages("s1"))
    assert messages[0].content == "hi"


def test_schema_drift_missing_optional_columns_are_tolerated(tmp_path: Path) -> None:
    """If a Hermes version omits ``ended_at`` or ``metadata``, the
    repository should still produce a usable HermesSession with those
    fields as None/empty."""
    db = tmp_path / "state.db"
    conn = sqlite3.connect(db)
    try:
        # Bare-minimum schema: only the required columns.
        conn.execute("CREATE TABLE sessions (session_id TEXT PRIMARY KEY, started_at TEXT)")
        conn.execute("CREATE TABLE messages (session_id TEXT, seq INTEGER, role TEXT, content TEXT)")
        conn.execute("INSERT INTO sessions VALUES ('s1', '2026-05-10T00:00:00Z')")
        conn.execute("INSERT INTO messages VALUES ('s1', 1, 'user', 'hello')")
        conn.commit()
    finally:
        conn.close()

    repo = HermesSessionRepository(db)
    session = next(iter(repo.iter_sessions()))
    assert session.session_id == "s1"
    assert session.ended_at is None
    assert session.agent_id is None
    assert session.metadata == {}
    message = next(iter(repo.iter_messages("s1")))
    assert message.timestamp is None
    assert message.metadata == {}


def test_repository_refuses_writes(tmp_path: Path) -> None:
    """The repository opens SQLite in read-only mode. Any attempt to
    write through its underlying connection raises. This is a
    load-bearing invariant per AC-706: the importer must never write
    to the Hermes session DB."""
    db = tmp_path / "state.db"
    _plant_session_db(
        db,
        sessions=[{"session_id": "s1", "started_at": "2026-05-10T00:00:00Z"}],
        messages=[],
    )
    repo = HermesSessionRepository(db)
    with pytest.raises(sqlite3.OperationalError):
        repo._connection.execute("INSERT INTO sessions VALUES ('x', 'x', 'x', 'x', 'x')")  # noqa: SLF001


def test_repository_works_without_wal_or_shm_sidecars(tmp_path: Path) -> None:
    """A Hermes DB exported without its WAL/SHM sidecars (a common
    "copy the .db file" support escalation) must still open. The
    repository should not require WAL to be initialized."""
    db = tmp_path / "state.db"
    _plant_session_db(
        db,
        sessions=[{"session_id": "s1", "started_at": "2026-05-10T00:00:00Z"}],
        messages=[],
    )
    # Make sure no sidecars are present.
    assert not (tmp_path / "state.db-wal").exists()
    assert not (tmp_path / "state.db-shm").exists()
    repo = HermesSessionRepository(db)
    assert [s.session_id for s in repo.iter_sessions()] == ["s1"]


def test_corrupt_metadata_json_falls_back_to_empty_dict(tmp_path: Path) -> None:
    """If ``sessions.metadata`` is not valid JSON, the repository
    should not blow up the whole iteration. Surface an empty dict and
    let the ingester continue."""
    db = tmp_path / "state.db"
    _plant_session_db(
        db,
        sessions=[
            {
                "session_id": "s1",
                "started_at": "2026-05-10T00:00:00Z",
                "metadata": "{not valid json",
            }
        ],
        messages=[],
    )
    repo = HermesSessionRepository(db)
    session = next(iter(repo.iter_sessions()))
    assert session.metadata == {}


def test_bare_minimum_sessions_table_without_started_at(tmp_path: Path) -> None:
    """PR #968 review (P2): a DB with only `sessions(session_id TEXT PRIMARY KEY)`
    should still iterate. The `ORDER BY started_at` clause must drop
    to a no-op when the column is absent (schema-drift posture)."""
    db = tmp_path / "state.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute("CREATE TABLE sessions (session_id TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE messages (session_id TEXT, seq INTEGER, role TEXT, content TEXT)")
        conn.execute("INSERT INTO sessions VALUES ('s1')")
        conn.execute("INSERT INTO sessions VALUES ('s2')")
        conn.commit()
    finally:
        conn.close()

    repo = HermesSessionRepository(db)
    sessions = list(repo.iter_sessions())
    assert {s.session_id for s in sessions} == {"s1", "s2"}
    assert all(s.started_at is None for s in sessions)


def test_messages_for_unknown_session_returns_empty(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    _plant_session_db(
        db,
        sessions=[{"session_id": "s1", "started_at": "2026-05-10T00:00:00Z"}],
        messages=[],
    )
    repo = HermesSessionRepository(db)
    assert list(repo.iter_messages("does-not-exist")) == []
