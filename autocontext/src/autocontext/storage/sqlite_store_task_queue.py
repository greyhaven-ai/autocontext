from __future__ import annotations

import json
import sqlite3
from typing import Any


class SQLiteTaskQueueStoreMixin:
    def connect(self) -> sqlite3.Connection:
        raise NotImplementedError

    # ---- Task Queue CRUD ----

    def enqueue_task(
        self,
        task_id: str,
        spec_name: str,
        priority: int = 0,
        config: dict[str, Any] | None = None,
        scheduled_at: str | None = None,
    ) -> None:
        """Add a task to the queue."""
        config_json = json.dumps(config) if config else None
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO task_queue(id, spec_name, priority, config_json, scheduled_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (task_id, spec_name, priority, config_json, scheduled_at),
            )

    def dequeue_task(self) -> dict[str, Any] | None:
        """Claim the highest-priority pending task.

        Returns the task row as a dict, or None if queue is empty.
        Uses a single UPDATE with subquery for true atomic dequeue —
        prevents double-processing under concurrent access.
        """
        with self.connect() as conn:
            # AC-906: single-statement claim (the ambient-queue idiom). The
            # UPDATE takes the write lock atomically, so two runners cannot
            # claim the same row, and attempts is burned AT CLAIM so a handler
            # that kills the process still counts toward the dead-letter limit.
            updated = conn.execute(
                """
                UPDATE task_queue
                SET status = 'running',
                    started_at = datetime('now'),
                    updated_at = datetime('now'),
                    attempts = attempts + 1
                WHERE id = (
                    SELECT id FROM task_queue
                    WHERE status = 'pending'
                      AND (scheduled_at IS NULL OR scheduled_at <= datetime('now'))
                    ORDER BY priority DESC, created_at ASC
                    LIMIT 1
                )
                RETURNING *
                """,
            ).fetchone()
            return dict(updated) if updated else None

    def complete_task(
        self,
        task_id: str,
        best_score: float,
        best_output: str,
        total_rounds: int,
        met_threshold: bool,
        result_json: str | None = None,
    ) -> None:
        """Mark a task as completed with results."""
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE task_queue
                SET status = 'completed',
                    completed_at = datetime('now'),
                    updated_at = datetime('now'),
                    best_score = ?,
                    best_output = ?,
                    total_rounds = ?,
                    met_threshold = ?,
                    result_json = ?
                WHERE id = ?
                """,
                (best_score, best_output, total_rounds, 1 if met_threshold else 0, result_json, task_id),
            )

    def requeue_stale_running(self, *, older_than_seconds: float, max_attempts: int | None = None) -> int:
        """Return crash-stranded running tasks to pending (AC-906).

        A crash between claim and complete/fail leaves the row in
        ``running`` forever; runners call this at startup. The claim-time
        attempts increment already counted the stranded execution, and with
        ``max_attempts`` set a row at/above the budget dead-letters here
        instead of requeueing, so a poison task that kills the process
        cannot crash-loop forever.
        """
        limit = max_attempts if max_attempts is not None else 2**31
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE task_queue
                SET status = CASE WHEN attempts >= ? THEN 'failed' ELSE 'pending' END,
                    error = CASE
                        WHEN attempts >= ? THEN COALESCE(error, 'crash-looped past the attempts limit')
                        ELSE error
                    END,
                    updated_at = datetime('now')
                WHERE status = 'running'
                  AND started_at IS NOT NULL
                  AND (julianday('now') - julianday(started_at)) * 86400.0 >= ?
                """,
                (limit, limit, older_than_seconds),
            )
            return int(cursor.rowcount)

    def fail_task(
        self,
        task_id: str,
        error: str,
        *,
        max_attempts: int | None = None,
        retry_backoff_s: float = 30.0,
    ) -> None:
        """Mark a task as failed, or requeue it below the attempts limit.

        With ``max_attempts`` set, a task whose claim count is still below
        the limit returns to ``pending`` (error recorded) for a later retry
        after a linear backoff of ``retry_backoff_s * attempts`` seconds
        (capped at 300s), so a provider outage is not burned through in
        seconds; at or above the limit it dead-letters to ``failed``.
        ``None`` keeps the legacy terminal behavior.
        """
        if max_attempts is not None:
            with self.connect() as conn:
                conn.execute(
                    """
                    UPDATE task_queue
                    SET status = CASE WHEN attempts >= ? THEN 'failed' ELSE 'pending' END,
                        completed_at = CASE WHEN attempts >= ? THEN datetime('now') ELSE NULL END,
                        scheduled_at = CASE
                            WHEN attempts >= ? THEN scheduled_at
                            ELSE datetime('now', '+' || CAST(min(300.0, ? * attempts) AS TEXT) || ' seconds')
                        END,
                        updated_at = datetime('now'),
                        error = ?
                    WHERE id = ?
                    """,
                    (max_attempts, max_attempts, max_attempts, retry_backoff_s, error, task_id),
                )
            return
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE task_queue
                SET status = 'failed',
                    completed_at = datetime('now'),
                    updated_at = datetime('now'),
                    error = ?
                WHERE id = ?
                """,
                (error, task_id),
            )

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        """Get a task by ID."""
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM task_queue WHERE id = ?", (task_id,)).fetchone()
            return dict(row) if row else None

    def list_tasks(
        self,
        status: str | None = None,
        spec_name: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List tasks with optional filters."""
        query = "SELECT * FROM task_queue WHERE 1=1"
        params: list[Any] = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if spec_name:
            query += " AND spec_name = ?"
            params.append(spec_name)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def pending_task_count(self) -> int:
        """Count pending tasks in the queue."""
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) as cnt FROM task_queue WHERE status = 'pending'").fetchone()
            return row["cnt"] if row else 0
