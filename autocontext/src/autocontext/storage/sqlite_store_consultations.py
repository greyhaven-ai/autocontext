from __future__ import annotations

import sqlite3
from typing import Any


class SQLiteConsultationStoreMixin:
    def connect(self) -> sqlite3.Connection:
        raise NotImplementedError

    # ---- Consultation Log (AC-212) ----

    def insert_consultation(
        self,
        run_id: str,
        generation_index: int,
        trigger: str,
        context_summary: str,
        critique: str,
        alternative_hypothesis: str,
        tiebreak_recommendation: str,
        suggested_next_action: str,
        raw_response: str,
        model_used: str,
        cost_usd: float | None,
    ) -> int:
        """Persist a consultation result. Returns the row id."""
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO consultation_log(
                    run_id, generation_index, trigger, context_summary,
                    critique, alternative_hypothesis, tiebreak_recommendation,
                    suggested_next_action, raw_response, model_used, cost_usd
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    generation_index,
                    trigger,
                    context_summary,
                    critique,
                    alternative_hypothesis,
                    tiebreak_recommendation,
                    suggested_next_action,
                    raw_response,
                    model_used,
                    cost_usd,
                ),
            )
            return cursor.lastrowid or 0

    def get_consultations_for_run(self, run_id: str) -> list[dict[str, Any]]:
        """Retrieve all consultation records for a run, ordered by generation."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, run_id, generation_index, trigger, context_summary,
                       critique, alternative_hypothesis, tiebreak_recommendation,
                       suggested_next_action, raw_response, model_used, cost_usd, created_at
                FROM consultation_log
                WHERE run_id = ?
                ORDER BY generation_index, id
                """,
                (run_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_total_consultation_cost(self, run_id: str) -> float:
        """Return total consultation cost for a run."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0.0) as total FROM consultation_log WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            return float(row["total"]) if row else 0.0
