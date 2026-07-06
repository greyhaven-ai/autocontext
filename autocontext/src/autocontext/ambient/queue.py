"""durable sqlite work queue connecting the ambient stages (task-queue idiom)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ambient_queue (
    job_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
)
"""


@dataclass(slots=True)
class AmbientJob:
    job_id: int
    stage: str
    kind: str
    payload: dict[str, Any]
    attempts: int


class AmbientQueue:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def enqueue(self, stage: str, kind: str, payload: dict[str, Any]) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO ambient_queue (stage, kind, payload) VALUES (?, ?, ?)",
                (stage, kind, json.dumps(payload)),
            )
            return int(cursor.lastrowid or 0)

    def claim(self, stage: str) -> AmbientJob | None:
        # single atomic statement: a select-then-update pair would let two
        # connections claim the same pending row (toctou double-claim)
        with self._connect() as conn:
            row = conn.execute(
                "UPDATE ambient_queue SET status = 'running' WHERE job_id = ("
                "SELECT job_id FROM ambient_queue WHERE stage = ? AND status = 'pending' "
                "ORDER BY job_id LIMIT 1) AND status = 'pending' "
                "RETURNING job_id, stage, kind, payload, attempts",
                (stage,),
            ).fetchone()
            if row is None:
                return None
            return AmbientJob(row[0], row[1], row[2], json.loads(row[3]), row[4])

    def requeue_stale_running(self) -> int:
        """return jobs stuck in running (for example after a crash) to pending.

        Assumes a single resident daemon per database: a second concurrent
        daemon calling this would clobber the first one's in-flight jobs.
        """
        with self._connect() as conn:
            cursor = conn.execute("UPDATE ambient_queue SET status = 'pending' WHERE status = 'running'")
            return int(cursor.rowcount)

    def complete(self, job_id: int) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE ambient_queue SET status = 'done' WHERE job_id = ?", (job_id,))

    def fail(self, job_id: int, error: str, *, max_attempts: int = 5) -> None:
        # a job that has failed max_attempts times is a poison job: move it to
        # 'dead' so it stops being reclaimed and looping forever. attempts is
        # incremented first so the count reflects this failure.
        with self._connect() as conn:
            conn.execute(
                "UPDATE ambient_queue SET attempts = attempts + 1, last_error = ? WHERE job_id = ?",
                (error, job_id),
            )
            conn.execute(
                "UPDATE ambient_queue SET status = CASE WHEN attempts >= ? THEN 'dead' ELSE 'pending' END WHERE job_id = ?",
                (max_attempts, job_id),
            )

    def dead_letter_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM ambient_queue WHERE status = 'dead'").fetchone()
            return int(row[0])

    def depth(self, stage: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM ambient_queue WHERE stage = ? AND status = 'pending'",
                (stage,),
            ).fetchone()
            return int(row[0])
