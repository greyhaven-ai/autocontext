"""SQLite access for the judge ledger: one row per real improvement-loop judge call (judge pre-screen Phase 0)."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from contextlib import AbstractContextManager
from typing import Any

JUDGE_LEDGER_COLUMNS = (
    "loop_id",
    "run_id",
    "scenario_name",
    "scenario_family",
    "round_number",
    "max_rounds",
    "quality_threshold",
    "evaluator_epoch",
    "rubric_hash",
    "task_prompt_hash",
    "task_prompt",
    "output_hash",
    "output",
    "required_concepts_json",
    "score",
    "passed",
    "dimension_scores_json",
    "internal_retries",
    "judge_failed",
    "previous_round_number",
    "previous_score",
    "previous_dimension_scores_json",
    "previous_output_hash",
    "fixture_provenance_json",
    "evaluator_guardrail_json",
    "prescreen_json",
)


class SQLiteJudgeLedgerStoreMixin:
    def connection(self) -> AbstractContextManager[sqlite3.Connection]:
        raise NotImplementedError

    def insert_judge_ledger_row(self, values: Mapping[str, Any]) -> int:
        """Append one judge verdict; unknown keys are rejected so a typo cannot silently drop data."""
        unknown = set(values) - set(JUDGE_LEDGER_COLUMNS)
        if unknown:
            raise ValueError(f"unknown judge_ledger columns: {sorted(unknown)}")
        columns = [column for column in JUDGE_LEDGER_COLUMNS if column in values]
        placeholders = ", ".join("?" for _ in columns)
        with self.connection() as conn:
            cursor = conn.execute(
                f"INSERT INTO judge_ledger({', '.join(columns)}) VALUES ({placeholders})",
                [values[column] for column in columns],
            )
            return cursor.lastrowid or 0

    def list_judge_ledger_rows(
        self,
        *,
        evaluator_epoch: str | None = None,
        scenario_family: str | None = None,
    ) -> list[dict[str, Any]]:
        """Rows in insertion order, optionally filtered by evaluator epoch and scenario family."""
        clauses: list[str] = []
        params: list[Any] = []
        if evaluator_epoch is not None:
            clauses.append("evaluator_epoch = ?")
            params.append(evaluator_epoch)
        if scenario_family is not None:
            clauses.append("scenario_family = ?")
            params.append(scenario_family)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connection() as conn:
            rows = conn.execute(f"SELECT * FROM judge_ledger {where} ORDER BY id", params).fetchall()
            return [dict(row) for row in rows]
