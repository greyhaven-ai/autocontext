from __future__ import annotations

import json
import math
import sqlite3
from contextlib import AbstractContextManager
from typing import Any


class SQLiteHumanFeedbackStoreMixin:
    def connection(self) -> AbstractContextManager[sqlite3.Connection]:
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
        if human_score is not None and not (math.isfinite(human_score) and 0.0 <= human_score <= 1.0):
            raise ValueError(f"human_score must be in [0.0, 1.0], got {human_score}")
        with self.connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO human_feedback(scenario_name, generation_id, agent_output, human_score, human_notes)
                VALUES (?, ?, ?, ?, ?)
                """,
                (scenario_name, generation_id, agent_output, human_score, human_notes),
            )
            return cursor.lastrowid or 0

    def get_human_feedback_by_acquisition_id(self, acquisition_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                """SELECT id, scenario_name, generation_id, agent_output, human_score, human_notes,
                          acquisition_id, reviewer, criterion_scores_json, created_at
                   FROM human_feedback WHERE acquisition_id = ?""",
                (acquisition_id,),
            ).fetchone()
            return dict(row) if row is not None else None

    def record_acquired_feedback(
        self, *, acquisition_id: str, scenario_name: str, agent_output: str, human_score: float,
        human_notes: str, reviewer: str, criterion_scores: dict[str, float] | None = None,
        generation_id: str | None = None, correction: bool = False,
    ) -> int:
        if not acquisition_id or not reviewer.strip() or not scenario_name or not agent_output:
            raise ValueError("acquisition feedback requires an id, reviewer, scenario and output")
        if isinstance(human_score, bool) or not math.isfinite(human_score) or not 0 <= human_score <= 1:
            raise ValueError("human_score must be in [0.0, 1.0]")
        scores = criterion_scores or {}
        if any(not isinstance(key, str) or not key or not isinstance(score, (int, float))
               or not math.isfinite(score) or not 0 <= score <= 1 for key, score in scores.items()):
            raise ValueError("criterion scores must be named values in [0.0, 1.0]")
        scores_json = json.dumps(scores, sort_keys=True)
        with self.connection() as conn:
            previous = conn.execute(
                """SELECT id, scenario_name, agent_output, human_score, human_notes,
                          reviewer, criterion_scores_json FROM human_feedback WHERE acquisition_id = ?""",
                (acquisition_id,),
            ).fetchone()
            if previous is not None:
                if previous["scenario_name"] != scenario_name or previous["agent_output"] != agent_output:
                    raise ValueError("acquisition identity is bound to another output")
                same = (previous["human_score"] == human_score and previous["human_notes"] == human_notes
                        and previous["reviewer"] == reviewer and previous["criterion_scores_json"] == scores_json)
                if same:
                    return int(previous["id"])
                if not correction:
                    raise ValueError("human acquisition label already exists; use explicit correction")
                conn.execute(
                    """UPDATE human_feedback SET human_score = ?, human_notes = ?, reviewer = ?, criterion_scores_json = ?
                       WHERE id = ?""",
                    (human_score, human_notes, reviewer, scores_json, previous["id"]),
                )
                return int(previous["id"])
            if correction:
                raise ValueError("cannot correct a missing human label")
            cursor = conn.execute(
                """INSERT INTO human_feedback(scenario_name, generation_id, agent_output, human_score, human_notes,
                                              acquisition_id, reviewer, criterion_scores_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (scenario_name, generation_id, agent_output, human_score, human_notes, acquisition_id, reviewer,
                 scores_json),
            )
            return cursor.lastrowid or 0

    def get_human_feedback(self, scenario_name: str, limit: int = 10) -> list[dict[str, Any]]:
        """Retrieve recent human feedback for a scenario."""
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT id, scenario_name, generation_id, agent_output, human_score, human_notes,
                       acquisition_id, reviewer, criterion_scores_json, created_at
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
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT id, scenario_name, agent_output, human_score, human_notes,
                       acquisition_id, reviewer, criterion_scores_json, created_at
                FROM human_feedback
                WHERE scenario_name = ? AND human_score IS NOT NULL AND human_notes != ''
                  AND acquisition_id IS NULL
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (scenario_name, limit),
            ).fetchall()
            return [dict(r) for r in rows]
