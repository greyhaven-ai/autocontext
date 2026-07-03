"""append-only trace store: every training-eligible record lands here after redaction."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ambient_traces (
    record_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    produced_by TEXT NOT NULL,
    redaction_findings INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ambient_source_cursors (
    source TEXT PRIMARY KEY,
    cursor TEXT NOT NULL
);
"""


@dataclass(slots=True)
class TraceRecord:
    record_id: int
    source: str
    kind: str
    payload: dict[str, Any]
    produced_by: str
    redaction_findings: int
    created_at: str


class TraceStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def append(self, source: str, kind: str, payload: dict[str, Any], produced_by: str, redaction_findings: int) -> int:
        created_at = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO ambient_traces (source, kind, payload, produced_by, redaction_findings, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (source, kind, json.dumps(payload), produced_by, redaction_findings, created_at),
            )
            return int(cursor.lastrowid or 0)

    def count(self, source: str | None = None) -> int:
        with self._connect() as conn:
            if source is None:
                row = conn.execute("SELECT COUNT(*) FROM ambient_traces").fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) FROM ambient_traces WHERE source = ?", (source,)).fetchone()
            return int(row[0])

    def recent(self, limit: int) -> list[TraceRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT record_id, source, kind, payload, produced_by, redaction_findings, created_at "
                "FROM ambient_traces ORDER BY record_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [TraceRecord(r[0], r[1], r[2], json.loads(r[3]), r[4], r[5], r[6]) for r in rows]

    def get_cursor(self, source: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT cursor FROM ambient_source_cursors WHERE source = ?", (source,)).fetchone()
            return None if row is None else str(row[0])

    def set_cursor(self, source: str, cursor: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO ambient_source_cursors (source, cursor) VALUES (?, ?) "
                "ON CONFLICT(source) DO UPDATE SET cursor = excluded.cursor",
                (source, cursor),
            )

    def db_size_bytes(self) -> int:
        return self.db_path.stat().st_size if self.db_path.exists() else 0

    def prune_oldest(self, fraction: float) -> int:
        total = self.count()
        to_delete = int(total * fraction)
        if to_delete <= 0:
            return 0
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM ambient_traces WHERE record_id IN (SELECT record_id FROM ambient_traces ORDER BY record_id LIMIT ?)",
                (to_delete,),
            )
        with self._connect() as conn:
            conn.execute("VACUUM")
        return to_delete
