from __future__ import annotations

import json
import sqlite3
from typing import Any


class SQLiteNotebookStoreMixin:
    def connect(self) -> sqlite3.Connection:
        raise NotImplementedError

    @staticmethod
    def _parse_json_field(raw: Any, default: Any) -> Any:
        raise NotImplementedError

    # ---- Session Notebook CRUD ----

    _NOTEBOOK_JSON_FIELDS = frozenset(
        {
            "current_hypotheses",
            "unresolved_questions",
            "operator_observations",
            "follow_ups",
        }
    )

    def _parse_notebook_row(self, row: dict[str, Any]) -> dict[str, Any]:
        """Parse JSON string fields in a notebook row back to lists."""
        result = dict(row)
        for field in self._NOTEBOOK_JSON_FIELDS:
            raw = result.get(field)
            if isinstance(raw, str):
                result[field] = self._parse_json_field(raw, [])
        return result

    def upsert_notebook(
        self,
        session_id: str,
        scenario_name: str,
        current_objective: str | None = None,
        current_hypotheses: list[str] | None = None,
        best_run_id: str | None = None,
        best_generation: int | None = None,
        best_score: float | None = None,
        unresolved_questions: list[str] | None = None,
        operator_observations: list[str] | None = None,
        follow_ups: list[str] | None = None,
    ) -> None:
        """Insert or update a session notebook."""
        existing = self.get_notebook(session_id)
        merged_current_objective = (
            current_objective
            if current_objective is not None
            else (str(existing["current_objective"]) if existing is not None else "")
        )
        merged_hypotheses = (
            current_hypotheses
            if current_hypotheses is not None
            else (list(existing["current_hypotheses"]) if existing is not None else [])
        )
        merged_best_run_id = (
            best_run_id
            if best_run_id is not None
            else (str(existing["best_run_id"]) if existing and existing.get("best_run_id") is not None else None)
        )
        merged_best_generation = (
            best_generation
            if best_generation is not None
            else (int(existing["best_generation"]) if existing and existing.get("best_generation") is not None else None)
        )
        merged_best_score = (
            best_score
            if best_score is not None
            else (float(existing["best_score"]) if existing and existing.get("best_score") is not None else None)
        )
        merged_questions = (
            unresolved_questions
            if unresolved_questions is not None
            else (list(existing["unresolved_questions"]) if existing is not None else [])
        )
        merged_observations = (
            operator_observations
            if operator_observations is not None
            else (list(existing["operator_observations"]) if existing is not None else [])
        )
        merged_follow_ups = (
            follow_ups if follow_ups is not None else (list(existing["follow_ups"]) if existing is not None else [])
        )

        hypotheses_json = json.dumps(merged_hypotheses)
        questions_json = json.dumps(merged_questions)
        observations_json = json.dumps(merged_observations)
        follow_ups_json = json.dumps(merged_follow_ups)

        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO session_notebooks(
                    session_id, scenario_name, current_objective, current_hypotheses,
                    best_run_id, best_generation, best_score,
                    unresolved_questions, operator_observations, follow_ups
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    scenario_name = excluded.scenario_name,
                    current_objective = excluded.current_objective,
                    current_hypotheses = excluded.current_hypotheses,
                    best_run_id = excluded.best_run_id,
                    best_generation = excluded.best_generation,
                    best_score = excluded.best_score,
                    unresolved_questions = excluded.unresolved_questions,
                    operator_observations = excluded.operator_observations,
                    follow_ups = excluded.follow_ups,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                """,
                (
                    session_id,
                    scenario_name,
                    merged_current_objective,
                    hypotheses_json,
                    merged_best_run_id,
                    merged_best_generation,
                    merged_best_score,
                    questions_json,
                    observations_json,
                    follow_ups_json,
                ),
            )

    def get_notebook(self, session_id: str) -> dict[str, Any] | None:
        """Get a session notebook by session id."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM session_notebooks WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            return self._parse_notebook_row(dict(row))

    def list_notebooks(self) -> list[dict[str, Any]]:
        """List all session notebooks."""
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM session_notebooks ORDER BY updated_at DESC").fetchall()
            return [self._parse_notebook_row(dict(r)) for r in rows]

    def list_notebooks_for_run(self, run_id: str) -> list[dict[str, Any]]:
        """List notebooks whose current best run matches the provided run id."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM session_notebooks WHERE best_run_id = ? ORDER BY updated_at DESC",
                (run_id,),
            ).fetchall()
            return [self._parse_notebook_row(dict(r)) for r in rows]

    def delete_notebook(self, session_id: str) -> bool:
        """Delete a session notebook. Returns True if a row was deleted."""
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM session_notebooks WHERE session_id = ?",
                (session_id,),
            )
            row = conn.execute("SELECT changes()").fetchone()
            return bool(row[0] > 0) if row else False
