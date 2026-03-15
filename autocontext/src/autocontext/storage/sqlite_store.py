from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

SQLITE_BUSY_TIMEOUT_MS = 5_000
AgentOutputBatch = tuple[str, str]
AgentRoleMetricBatch = tuple[str, str, int, int, int, str, str]


class SQLiteStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=SQLITE_BUSY_TIMEOUT_MS / 1000)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS};")
        return conn

    def migrate(self, migrations_dir: Path) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                """
            )
            for migration in sorted(migrations_dir.glob("*.sql")):
                applied = conn.execute(
                    "SELECT version FROM schema_migrations WHERE version = ?",
                    (migration.name,),
                ).fetchone()
                if applied:
                    continue
                conn.executescript(migration.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO schema_migrations(version) VALUES (?)", (migration.name,))

    def create_run(self, run_id: str, scenario: str, generations: int, executor_mode: str, agent_provider: str = "") -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO runs(run_id, scenario, target_generations, executor_mode, status, agent_provider)
                VALUES (?, ?, ?, ?, 'running', ?)
                """,
                (run_id, scenario, generations, executor_mode, agent_provider),
            )

    def generation_exists(self, run_id: str, generation_index: int) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM generations WHERE run_id = ? AND generation_index = ?",
                (run_id, generation_index),
            ).fetchone()
            return row is not None

    def upsert_generation(
        self,
        run_id: str,
        generation_index: int,
        mean_score: float,
        best_score: float,
        elo: float,
        wins: int,
        losses: int,
        gate_decision: str,
        status: str,
        duration_seconds: float | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO generations(
                    run_id, generation_index, mean_score, best_score, elo, wins, losses,
                    gate_decision, status, duration_seconds
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, generation_index) DO UPDATE SET
                    mean_score = excluded.mean_score,
                    best_score = excluded.best_score,
                    elo = excluded.elo,
                    wins = excluded.wins,
                    losses = excluded.losses,
                    gate_decision = excluded.gate_decision,
                    status = excluded.status,
                    duration_seconds = excluded.duration_seconds,
                    updated_at = datetime('now')
                """,
                (
                    run_id,
                    generation_index,
                    mean_score,
                    best_score,
                    elo,
                    wins,
                    losses,
                    gate_decision,
                    status,
                    duration_seconds,
                ),
            )

    def update_generation_duration(
        self,
        run_id: str,
        generation_index: int,
        duration_seconds: float,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE generations
                SET duration_seconds = ?, updated_at = datetime('now')
                WHERE run_id = ? AND generation_index = ?
                """,
                (duration_seconds, run_id, generation_index),
            )

    def insert_match(
        self,
        run_id: str,
        generation_index: int,
        seed: int,
        score: float,
        passed_validation: bool,
        validation_errors: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO matches(run_id, generation_index, seed, score, passed_validation, validation_errors)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (run_id, generation_index, seed, score, int(passed_validation), validation_errors),
            )

    def insert_staged_validation_results(
        self,
        run_id: str,
        generation_index: int,
        results: list[dict[str, Any]],
    ) -> None:
        """Persist per-stage validation results from the staged pipeline."""
        if not results:
            return
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO staged_validation_results(
                    run_id, generation_index, stage_order, stage_name,
                    status, duration_ms, error, error_code
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        generation_index,
                        r["stage_order"],
                        r["stage_name"],
                        r["status"],
                        r["duration_ms"],
                        r.get("error"),
                        r.get("error_code"),
                    )
                    for r in results
                ],
            )

    def get_staged_validation_results(
        self,
        run_id: str,
        generation_index: int,
    ) -> list[dict[str, Any]]:
        """Retrieve staged validation results for a generation, ordered by stage."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT stage_order, stage_name, status, duration_ms, error, error_code
                FROM staged_validation_results
                WHERE run_id = ? AND generation_index = ?
                ORDER BY stage_order
                """,
                (run_id, generation_index),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_staged_validation_results_for_run(self, run_id: str) -> list[dict[str, Any]]:
        """Retrieve all staged validation results for a run, ordered by generation and stage."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT generation_index, stage_order, stage_name, status, duration_ms, error, error_code, created_at
                FROM staged_validation_results
                WHERE run_id = ?
                ORDER BY generation_index, stage_order
                """,
                (run_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def append_agent_output(self, run_id: str, generation_index: int, role: str, content: str) -> None:
        self.append_generation_agent_activity(
            run_id,
            generation_index,
            outputs=[(role, content)],
            role_metrics=[],
        )

    def append_agent_outputs(
        self,
        run_id: str,
        generation_index: int,
        outputs: Sequence[AgentOutputBatch],
    ) -> None:
        self.append_generation_agent_activity(
            run_id,
            generation_index,
            outputs=outputs,
            role_metrics=[],
        )

    def _append_agent_outputs(
        self,
        conn: sqlite3.Connection,
        run_id: str,
        generation_index: int,
        outputs: Sequence[AgentOutputBatch],
    ) -> None:
        if not outputs:
            return
        conn.executemany(
            """
            INSERT INTO agent_outputs(run_id, generation_index, role, content)
            VALUES (?, ?, ?, ?)
            """,
            [(run_id, generation_index, role, content) for role, content in outputs],
        )

    def get_agent_outputs_by_role(self, run_id: str, role: str) -> list[dict[str, object]]:
        """Return agent_outputs rows for a given run and role, ordered by generation."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT generation_index, role, content
                FROM agent_outputs
                WHERE run_id = ? AND role = ?
                ORDER BY generation_index, rowid
                """,
                (run_id, role),
            ).fetchall()
            return [dict(r) for r in rows]

    def append_agent_role_metric(
        self,
        run_id: str,
        generation_index: int,
        role: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: int,
        subagent_id: str,
        status: str,
    ) -> None:
        self.append_generation_agent_activity(
            run_id,
            generation_index,
            outputs=[],
            role_metrics=[(
                role,
                model,
                input_tokens,
                output_tokens,
                latency_ms,
                subagent_id,
                status,
            )],
        )

    def append_agent_role_metrics(
        self,
        run_id: str,
        generation_index: int,
        role_metrics: Sequence[AgentRoleMetricBatch],
    ) -> None:
        self.append_generation_agent_activity(
            run_id,
            generation_index,
            outputs=[],
            role_metrics=role_metrics,
        )

    def _append_agent_role_metrics(
        self,
        conn: sqlite3.Connection,
        run_id: str,
        generation_index: int,
        role_metrics: Sequence[AgentRoleMetricBatch],
    ) -> None:
        if not role_metrics:
            return
        conn.executemany(
            """
            INSERT INTO agent_role_metrics(
                run_id, generation_index, role, model, input_tokens, output_tokens, latency_ms, subagent_id, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    run_id,
                    generation_index,
                    role,
                    model,
                    input_tokens,
                    output_tokens,
                    latency_ms,
                    subagent_id,
                    status,
                )
                for role, model, input_tokens, output_tokens, latency_ms, subagent_id, status in role_metrics
            ],
        )

    def append_generation_agent_activity(
        self,
        run_id: str,
        generation_index: int,
        outputs: Sequence[AgentOutputBatch],
        role_metrics: Sequence[AgentRoleMetricBatch],
    ) -> None:
        if not outputs and not role_metrics:
            return
        with self.connect() as conn:
            self._append_agent_outputs(conn, run_id, generation_index, outputs)
            self._append_agent_role_metrics(conn, run_id, generation_index, role_metrics)

    def append_recovery_marker(
        self,
        run_id: str,
        generation_index: int,
        decision: str,
        reason: str,
        retry_count: int,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO generation_recovery(run_id, generation_index, decision, reason, retry_count)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, generation_index, decision, reason, retry_count),
            )

    def get_recovery_markers_for_run(self, run_id: str) -> list[dict[str, Any]]:
        """Return recovery markers for a run, ordered by generation."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT generation_index, decision, reason, retry_count, created_at
                FROM generation_recovery
                WHERE run_id = ?
                ORDER BY generation_index, rowid
                """,
                (run_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_matches_for_run(self, run_id: str) -> list[dict[str, Any]]:
        """Return all match records for a run, ordered by generation and seed."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM matches WHERE run_id = ? ORDER BY generation_index, seed",
                (run_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_generation_metrics(self, run_id: str) -> list[dict[str, Any]]:
        """Return all generation records for a run, ordered by generation index."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM generations WHERE run_id = ? ORDER BY generation_index",
                (run_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_agent_role_metrics(self, run_id: str) -> list[dict[str, Any]]:
        """Return agent role metrics for a run, ordered by generation and row id."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT generation_index, role, model, input_tokens, output_tokens,
                       latency_ms, subagent_id, status, created_at
                FROM agent_role_metrics
                WHERE run_id = ?
                ORDER BY generation_index, rowid
                """,
                (run_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_generation_trajectory(self, run_id: str) -> list[dict[str, Any]]:
        """Return generation trajectory with score deltas."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT generation_index, mean_score, best_score, elo, gate_decision
                FROM generations
                WHERE run_id = ? AND status = 'completed'
                ORDER BY generation_index
                """,
                (run_id,),
            ).fetchall()
            result = []
            prev_best = 0.0
            for row in rows:
                d = dict(row)
                d["delta"] = round(d["best_score"] - prev_best, 6)
                prev_best = d["best_score"]
                result.append(d)
            return result

    def get_strategy_score_history(self, run_id: str) -> list[dict[str, Any]]:
        """Return strategy content with scores, joining agent_outputs and generations."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT ao.generation_index, ao.content, g.best_score, g.gate_decision
                FROM agent_outputs ao
                JOIN generations g ON ao.run_id = g.run_id AND ao.generation_index = g.generation_index
                JOIN (
                    SELECT run_id, generation_index, MAX(rowid) AS max_rowid
                    FROM agent_outputs
                    WHERE role = 'competitor'
                    GROUP BY run_id, generation_index
                ) latest ON ao.run_id = latest.run_id
                    AND ao.generation_index = latest.generation_index
                    AND ao.rowid = latest.max_rowid
                WHERE ao.run_id = ? AND ao.role = 'competitor' AND g.status = 'completed'
                ORDER BY ao.generation_index
                """,
                (run_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def save_knowledge_snapshot(
        self,
        scenario: str,
        run_id: str,
        best_score: float,
        best_elo: float,
        playbook_hash: str,
        agent_provider: str = "",
        rlm_enabled: bool = False,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO knowledge_snapshots(
                    scenario, run_id, best_score, best_elo, playbook_hash, agent_provider, rlm_enabled
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (scenario, run_id, best_score, best_elo, playbook_hash, agent_provider, int(rlm_enabled)),
            )

    def get_best_knowledge_snapshot(self, scenario: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT scenario, run_id, best_score, best_elo, playbook_hash, created_at
                FROM knowledge_snapshots
                WHERE scenario = ?
                ORDER BY best_score DESC
                LIMIT 1
                """,
                (scenario,),
            ).fetchone()
            return dict(row) if row else None

    def get_ecosystem_snapshots(self, scenario: str) -> list[dict[str, Any]]:
        """Return all knowledge snapshots for a scenario with provider info, ordered by created_at ASC."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT scenario, run_id, best_score, best_elo, playbook_hash, agent_provider, rlm_enabled, created_at
                FROM knowledge_snapshots
                WHERE scenario = ?
                ORDER BY id ASC
                """,
                (scenario,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_best_competitor_output(self, scenario: str) -> str | None:
        """Return the competitor output from the best-scoring generation across all runs for a scenario."""
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT ao.content
                FROM agent_outputs ao
                JOIN generations g ON ao.run_id = g.run_id AND ao.generation_index = g.generation_index
                JOIN runs r ON g.run_id = r.run_id
                JOIN (
                    SELECT run_id, generation_index, MAX(rowid) AS max_rowid
                    FROM agent_outputs
                    WHERE role = 'competitor'
                    GROUP BY run_id, generation_index
                ) latest ON ao.run_id = latest.run_id
                    AND ao.generation_index = latest.generation_index
                    AND ao.rowid = latest.max_rowid
                WHERE r.scenario = ? AND ao.role = 'competitor' AND g.status = 'completed'
                ORDER BY g.best_score DESC
                LIMIT 1
                """,
                (scenario,),
            ).fetchone()
            return row["content"] if row else None

    def count_completed_runs(self, scenario: str) -> int:
        """Return count of completed runs for a scenario."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM runs WHERE scenario = ? AND status = 'completed'",
                (scenario,),
            ).fetchone()
            return row["cnt"] if row else 0

    def mark_run_completed(self, run_id: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE runs SET status = 'completed', updated_at = datetime('now') WHERE run_id = ?", (run_id,))

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
        import json as _json

        config_json = _json.dumps(config) if config else None
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

            updated = conn.execute(
                "SELECT * FROM task_queue WHERE id = ?", (task_id,)
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
            row = conn.execute(
                "SELECT * FROM task_queue WHERE id = ?", (task_id,)
            ).fetchone()
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
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM task_queue WHERE status = 'pending'"
            ).fetchone()
            return row["cnt"] if row else 0

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
                    run_id, generation_index, trigger, context_summary,
                    critique, alternative_hypothesis, tiebreak_recommendation,
                    suggested_next_action, raw_response, model_used, cost_usd,
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

    # ---- Session Notebook CRUD ----

    _NOTEBOOK_JSON_FIELDS = frozenset({
        "current_hypotheses",
        "unresolved_questions",
        "operator_observations",
        "follow_ups",
    })

    def _parse_notebook_row(self, row: dict[str, Any]) -> dict[str, Any]:
        """Parse JSON string fields in a notebook row back to lists."""
        import json as _json

        result = dict(row)
        for field in self._NOTEBOOK_JSON_FIELDS:
            raw = result.get(field)
            if isinstance(raw, str):
                try:
                    result[field] = _json.loads(raw)
                except (ValueError, TypeError):
                    result[field] = []
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
        import json as _json

        existing = self.get_notebook(session_id)
        merged_current_objective = current_objective if current_objective is not None else (
            str(existing["current_objective"]) if existing is not None else ""
        )
        merged_hypotheses = current_hypotheses if current_hypotheses is not None else (
            list(existing["current_hypotheses"]) if existing is not None else []
        )
        merged_best_run_id = best_run_id if best_run_id is not None else (
            str(existing["best_run_id"]) if existing and existing.get("best_run_id") is not None else None
        )
        merged_best_generation = best_generation if best_generation is not None else (
            int(existing["best_generation"]) if existing and existing.get("best_generation") is not None else None
        )
        merged_best_score = best_score if best_score is not None else (
            float(existing["best_score"]) if existing and existing.get("best_score") is not None else None
        )
        merged_questions = unresolved_questions if unresolved_questions is not None else (
            list(existing["unresolved_questions"]) if existing is not None else []
        )
        merged_observations = operator_observations if operator_observations is not None else (
            list(existing["operator_observations"]) if existing is not None else []
        )
        merged_follow_ups = follow_ups if follow_ups is not None else (
            list(existing["follow_ups"]) if existing is not None else []
        )

        hypotheses_json = _json.dumps(merged_hypotheses)
        questions_json = _json.dumps(merged_questions)
        observations_json = _json.dumps(merged_observations)
        follow_ups_json = _json.dumps(merged_follow_ups)

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
            rows = conn.execute(
                "SELECT * FROM session_notebooks ORDER BY updated_at DESC"
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

    def get_run_best_score(self, run_id: str) -> float | None:
        """Return the best score recorded for a run, if any."""
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT MAX(best_score) AS best_score
                FROM generations
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            if row is None or row["best_score"] is None:
                return None
            return float(row["best_score"])

    # ---- Monitor Conditions + Alerts (AC-209) ----

    def insert_monitor_condition(self, condition: object) -> str:
        """Persist a MonitorCondition. Returns the condition id."""
        import json as _json

        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO monitor_conditions(id, name, condition_type, params_json, scope, active)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    condition.id,  # type: ignore[attr-defined]
                    condition.name,  # type: ignore[attr-defined]
                    str(condition.condition_type),  # type: ignore[attr-defined]
                    _json.dumps(condition.params),  # type: ignore[attr-defined]
                    condition.scope,  # type: ignore[attr-defined]
                    1 if condition.active else 0,  # type: ignore[attr-defined]
                ),
            )
            return str(condition.id)  # type: ignore[attr-defined]

    def list_monitor_conditions(
        self,
        *,
        active_only: bool = True,
        scope: str | None = None,
    ) -> list[dict[str, Any]]:
        """List monitor conditions with optional filters. Returns parsed params."""
        import json as _json

        query = "SELECT * FROM monitor_conditions WHERE 1=1"
        params: list[Any] = []
        if active_only:
            query += " AND active = 1"
        if scope is not None:
            query += " AND scope = ?"
            params.append(scope)
        query += " ORDER BY created_at DESC"
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
            results = []
            for row in rows:
                d = dict(row)
                raw_params = d.pop("params_json", "{}")
                d["params"] = _json.loads(raw_params) if isinstance(raw_params, str) else {}
                results.append(d)
            return results

    def count_monitor_conditions(
        self,
        *,
        active_only: bool = True,
        scope: str | None = None,
    ) -> int:
        """Count monitor conditions with optional filters."""
        query = "SELECT COUNT(*) AS cnt FROM monitor_conditions WHERE 1=1"
        params: list[Any] = []
        if active_only:
            query += " AND active = 1"
        if scope is not None:
            query += " AND scope = ?"
            params.append(scope)
        with self.connect() as conn:
            row = conn.execute(query, params).fetchone()
            return int(row["cnt"]) if row is not None else 0

    def get_monitor_condition(self, condition_id: str) -> dict[str, Any] | None:
        """Get a single monitor condition by id. Returns parsed params."""
        import json as _json

        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM monitor_conditions WHERE id = ?",
                (condition_id,),
            ).fetchone()
            if row is None:
                return None
            d = dict(row)
            raw_params = d.pop("params_json", "{}")
            d["params"] = _json.loads(raw_params) if isinstance(raw_params, str) else {}
            return d

    def deactivate_monitor_condition(self, condition_id: str) -> bool:
        """Deactivate a monitor condition. Returns True if found and updated."""
        with self.connect() as conn:
            conn.execute(
                "UPDATE monitor_conditions SET active = 0 WHERE id = ?",
                (condition_id,),
            )
            row = conn.execute("SELECT changes()").fetchone()
            return bool(row[0] > 0) if row else False

    def insert_monitor_alert(self, alert: object) -> str:
        """Persist a MonitorAlert. Returns the alert id."""
        import json as _json

        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO monitor_alerts(id, condition_id, condition_name, condition_type,
                    scope, detail, payload_json, fired_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    alert.id,  # type: ignore[attr-defined]
                    alert.condition_id,  # type: ignore[attr-defined]
                    alert.condition_name,  # type: ignore[attr-defined]
                    str(alert.condition_type),  # type: ignore[attr-defined]
                    alert.scope,  # type: ignore[attr-defined]
                    alert.detail,  # type: ignore[attr-defined]
                    _json.dumps(alert.payload),  # type: ignore[attr-defined]
                    alert.fired_at,  # type: ignore[attr-defined]
                ),
            )
            return str(alert.id)  # type: ignore[attr-defined]

    def list_monitor_alerts(
        self,
        *,
        condition_id: str | None = None,
        scope: str | None = None,
        limit: int = 100,
        since: str | None = None,
    ) -> list[dict[str, Any]]:
        """List monitor alerts with optional filters. Returns parsed payload."""
        import json as _json

        query = "SELECT * FROM monitor_alerts WHERE 1=1"
        params: list[Any] = []
        if condition_id is not None:
            query += " AND condition_id = ?"
            params.append(condition_id)
        if scope is not None:
            query += " AND scope = ?"
            params.append(scope)
        if since is not None:
            query += " AND fired_at >= ?"
            params.append(since)
        query += " ORDER BY fired_at DESC LIMIT ?"
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
            results = []
            for row in rows:
                d = dict(row)
                raw_payload = d.pop("payload_json", "{}")
                d["payload"] = _json.loads(raw_payload) if isinstance(raw_payload, str) else {}
                results.append(d)
            return results

    def get_latest_monitor_alert(self, condition_id: str) -> dict[str, Any] | None:
        """Return the newest alert for a condition, if one exists."""
        alerts = self.list_monitor_alerts(condition_id=condition_id, limit=1)
        return alerts[0] if alerts else None
