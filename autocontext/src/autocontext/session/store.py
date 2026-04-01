"""Session persistence store (AC-507).

SQLite-backed storage for session aggregate roots.
Stores full session state as JSON for simplicity — the session
is small enough that document-style storage is appropriate.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from autocontext.session.types import Session


class SessionStore:
    """Persists and retrieves session aggregates."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    goal TEXT NOT NULL,
                    status TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT ''
                )
            """)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def save(self, session: Session) -> None:
        """Persist a session (insert or update)."""
        data = session.model_dump_json()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (session_id, goal, status, data_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    status = excluded.status,
                    data_json = excluded.data_json,
                    updated_at = excluded.updated_at
                """,
                (session.session_id, session.goal, session.status, data,
                 session.created_at, session.updated_at),
            )

    def load(self, session_id: str) -> Session | None:
        """Load a session by ID. Returns None if not found."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT data_json FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return Session.model_validate_json(row["data_json"])

    def list(self, status: str | None = None, limit: int = 50) -> list[Session]:
        """List sessions, newest first."""
        query = "SELECT data_json FROM sessions"
        params: list[str | int] = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [Session.model_validate_json(row["data_json"]) for row in rows]

    def delete(self, session_id: str) -> bool:
        """Delete a session. Returns True if found and deleted."""
        with self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            row = conn.execute("SELECT changes()").fetchone()
            return bool(row[0] > 0) if row else False
