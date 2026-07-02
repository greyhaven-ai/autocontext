from __future__ import annotations

import sqlite3
from typing import Any


class SQLiteHumanFeedbackStoreMixin:
    def connect(self) -> sqlite3.Connection:
        raise NotImplementedError

    # -- Human feedback --

    def insert_human_feedback(
        self,
        scenario_name: str,
        agent_output: str,
        human_score: float | None = None,
        human_notes: str = "",
        generation_id: str | None = None,
    ) -> int:
        """Store human feedback on an agent task output. Returns the row id."""
        if human_score is not None and not (0.0 <= human_score <= 1.0):
            raise ValueError(f"human_score must be in [0.0, 1.0], got {human_score}")
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO human_feedback(scenario_name, generation_id, agent_output, human_score, human_notes)
                VALUES (?, ?, ?, ?, ?)
                """,
                (scenario_name, generation_id, agent_output, human_score, human_notes),
            )
            return cursor.lastrowid or 0

    def get_human_feedback(self, scenario_name: str, limit: int = 10) -> list[dict[str, Any]]:
        """Retrieve recent human feedback for a scenario."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, scenario_name, generation_id, agent_output, human_score, human_notes, created_at
                FROM human_feedback
                WHERE scenario_name = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (scenario_name, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_calibration_examples(self, scenario_name: str, limit: int = 5) -> list[dict[str, Any]]:
        """Retrieve feedback with both score and notes — suitable for judge calibration."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, scenario_name, agent_output, human_score, human_notes, created_at
                FROM human_feedback
                WHERE scenario_name = ? AND human_score IS NOT NULL AND human_notes != ''
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (scenario_name, limit),
            ).fetchall()
            return [dict(r) for r in rows]
