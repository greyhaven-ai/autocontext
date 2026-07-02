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
            # Atomic: SELECT the best candidate, then UPDATE in one transaction.
            # SQLite's write lock on the transaction prevents two connections
            # from claiming the same row.
            row = conn.execute(
                """
                SELECT id FROM task_queue
                WHERE status = 'pending'
                  AND (scheduled_at IS NULL OR scheduled_at <= datetime('now'))
                ORDER BY priority DESC, created_at ASC
                LIMIT 1
                """,
            ).fetchone()
            if not row:
                return None

            task_id = row["id"]
            conn.execute(
                """
                UPDATE task_queue
                SET status = 'running',
                    started_at = datetime('now'),
                    updated_at = datetime('now')
                WHERE id = ? AND status = 'pending'
                """,
                (task_id,),
            )
            # Check we actually claimed it (another runner might have beaten us)
            if conn.execute("SELECT changes()").fetchone()[0] == 0:
                return None

            updated = conn.execute("SELECT * FROM task_queue WHERE id = ?", (task_id,)).fetchone()
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

    def fail_task(self, task_id: str, error: str) -> None:
        """Mark a task as failed."""
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
