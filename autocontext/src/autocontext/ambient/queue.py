"""durable sqlite work queue for ambient work (task-queue idiom).

Production status (AC-906 audit): the ambient stages are cursor-driven and
do not route work through this queue today; the daemon uses it for
crash-recovery bookkeeping (requeue_stale_running under the startup flock)
and it serves as the claiming reference implementation. Routing stage work
through it, or removing it, is an ambient-roadmap decision; until then its
claim/dead-letter semantics are kept correct so adoption is safe.
"""

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

    def claim(self, stage: str, *, max_attempts: int = 5) -> AmbientJob | None:
        # single atomic statement: a select-then-update pair would let two
        # connections claim the same pending row (toctou double-claim)
        with self._connect() as conn:
            # AC-906: attempts is burned AT CLAIM, and a job that already
            # exhausted its budget dead-letters here. A handler that kills the
            # process never calls fail(), so counting at fail time alone let
            # crash-loops run forever without reaching the poison-job cap.
            conn.execute(
                "UPDATE ambient_queue SET status = 'dead' "
                "WHERE stage = ? AND status = 'pending' AND attempts >= ?",
                (stage, max_attempts),
            )
            row = conn.execute(
                "UPDATE ambient_queue SET status = 'running', attempts = attempts + 1 WHERE job_id = ("
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
        # counted at CLAIM time (AC-906), so this only records the error and
        # applies the cap.
        with self._connect() as conn:
            conn.execute(
                "UPDATE ambient_queue SET last_error = ? WHERE job_id = ?",
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
