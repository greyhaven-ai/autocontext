from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from autocontext.storage.bootstrap_schema import bootstrap_core_schema, default_migrations_dir
from autocontext.storage.row_types import GenerationMetricsRow, GenerationScoreRevisionRow, RunRow
from autocontext.storage.sqlite_migrations import apply_python_migration_files
from autocontext.storage.sqlite_store_consultations import SQLiteConsultationStoreMixin
from autocontext.storage.sqlite_store_hub import SQLiteHubStoreMixin
from autocontext.storage.sqlite_store_human_feedback import SQLiteHumanFeedbackStoreMixin
from autocontext.storage.sqlite_store_monitoring import SQLiteMonitorStoreMixin
from autocontext.storage.sqlite_store_notebooks import SQLiteNotebookStoreMixin
from autocontext.storage.sqlite_store_task_queue import SQLiteTaskQueueStoreMixin

SQLITE_BUSY_TIMEOUT_MS = 5_000
AgentOutputBatch = tuple[str, str]
AgentRoleMetricBatch = tuple[str, str, int, int, int, str, str]


class SQLiteStore(
    SQLiteHubStoreMixin,
    SQLiteMonitorStoreMixin,
    SQLiteTaskQueueStoreMixin,
    SQLiteConsultationStoreMixin,
    SQLiteHumanFeedbackStoreMixin,
    SQLiteNotebookStoreMixin,
):
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=SQLITE_BUSY_TIMEOUT_MS / 1000)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS};")
        return conn

    def ensure_core_tables(self) -> None:
        """Create the current core schema when migration files are unavailable."""
        migrations_dir = default_migrations_dir()
        if migrations_dir.exists() and any(migrations_dir.glob("*.sql")):
            self.migrate(migrations_dir)
            return
        with self.connect() as conn:
            bootstrap_core_schema(conn)

    def migrate(self, migrations_dir: Path) -> None:
        if not migrations_dir.exists() or not any(migrations_dir.glob("*.sql")):
            with self.connect() as conn:
                bootstrap_core_schema(conn)
            return
        with self.connect() as conn:
            apply_python_migration_files(conn, migrations_dir)

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

    def get_generation(self, run_id: str, generation_index: int) -> dict[str, Any] | None:
        """Return a single generation row by run_id and index."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM generations WHERE run_id = ? AND generation_index = ?",
                (run_id, generation_index),
            ).fetchone()
            return dict(row) if row else None

    def update_generation_status(
        self,
        run_id: str,
        generation_index: int,
        *,
        status: str,
        gate_decision: str,
    ) -> None:
        """Update only the terminal state fields for an existing generation row."""
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE generations
                SET status = ?,
                    gate_decision = ?,
                    updated_at = datetime('now')
                WHERE run_id = ? AND generation_index = ?
                """,
                (status, gate_decision, run_id, generation_index),
            )

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
        dimension_summary_json: str | None = None,
        scoring_backend: str = "elo",
        rating_uncertainty: float | None = None,
        evaluator_epoch: str | None = None,
        quarantined: bool | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO generations(
                    run_id, generation_index, mean_score, best_score, elo, wins, losses,
                    gate_decision, status, duration_seconds, dimension_summary_json,
                    scoring_backend, rating_uncertainty, evaluator_epoch, quarantined
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, generation_index) DO UPDATE SET
                    mean_score = excluded.mean_score,
                    best_score = excluded.best_score,
                    elo = excluded.elo,
                    wins = excluded.wins,
                    losses = excluded.losses,
                    gate_decision = excluded.gate_decision,
                    status = excluded.status,
                    duration_seconds = excluded.duration_seconds,
                    dimension_summary_json = excluded.dimension_summary_json,
                    scoring_backend = excluded.scoring_backend,
                    rating_uncertainty = excluded.rating_uncertainty,
                    evaluator_epoch = COALESCE(excluded.evaluator_epoch, evaluator_epoch),
                    quarantined = COALESCE(excluded.quarantined, quarantined),
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
                    dimension_summary_json,
                    scoring_backend,
                    rating_uncertainty,
                    evaluator_epoch,
                    quarantined,
                ),
            )

    def clear_quarantine_for_epoch(self, scenario: str, epoch_id: str) -> int:
        """Clear the quarantine marker on a scenario's generation rows scored under ``epoch_id``.

        Scoped by scenario (via the runs table) so a content-hash epoch shared across scenarios only
        clears the promoted scenario's rows. Returns the number of rows cleared.
        """
        with self.connect() as conn:
            cur = conn.execute(
                "UPDATE generations SET quarantined = NULL "
                "WHERE evaluator_epoch = ? AND quarantined IS NOT NULL "
                "AND run_id IN (SELECT run_id FROM runs WHERE scenario = ?)",
                (epoch_id, scenario),
            )
            return cur.rowcount

    def record_rescore_revision(
        self,
        run_id: str,
        generation_index: int,
        new_score: float,
        new_epoch: str,
        *,
        created_by: str | None = None,
    ) -> bool:
        """Append an audit revision recording a re-score. Returns False if the generation is absent.

        Append-only and non-destructive: this records the fresh ``(revision_epoch, revision_score)`` and
        archives the generation's CURRENT ``(evaluator_epoch, best_score, quarantined)`` as the ``previous_*``
        values, in a single atomic ``INSERT ... SELECT`` (so there is no read-then-write gap and no
        concurrent writer can be lost). It does NOT modify the ``generations`` row, its quarantine marker, or
        any derived table (``knowledge_snapshots`` etc.); the live score of record is left untouched. The
        ``SELECT`` matches no row when the generation does not exist, so nothing is inserted (returns False).
        """
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO generation_score_revisions "
                "(run_id, generation_index, revision_epoch, revision_score, previous_epoch, "
                " previous_score, previous_quarantined, created_by) "
                "SELECT ?, ?, ?, ?, evaluator_epoch, best_score, quarantined, ? "
                "FROM generations WHERE run_id = ? AND generation_index = ?",
                (run_id, generation_index, new_epoch, new_score, created_by, run_id, generation_index),
            )
            return cur.rowcount > 0

    def list_rescore_revisions(
        self,
        run_id: str,
        generation_index: int,
    ) -> list[GenerationScoreRevisionRow]:
        """Return the archived score revisions for a generation, oldest first."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, run_id, generation_index, revision_epoch, revision_score, "
                "previous_epoch, previous_score, previous_quarantined, created_by, created_at "
                "FROM generation_score_revisions "
                "WHERE run_id = ? AND generation_index = ? ORDER BY id",
                (run_id, generation_index),
            ).fetchall()
            return [cast(GenerationScoreRevisionRow, dict(row)) for row in rows]

    def latest_active_revisions(self, run_id: str, active_epoch: str | None) -> dict[int, GenerationScoreRevisionRow]:
        """Return, per generation_index, the LATEST revision recorded under ``active_epoch`` for the run.

        Only revisions whose ``revision_epoch`` equals ``active_epoch`` are considered; the most recent
        (highest ``id``) wins. Empty when ``active_epoch`` is None or nothing matches. Read-only.
        """
        if active_epoch is None:
            return {}
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, run_id, generation_index, revision_epoch, revision_score, "
                "previous_epoch, previous_score, previous_quarantined, created_by, created_at "
                "FROM generation_score_revisions "
                "WHERE run_id = ? AND revision_epoch = ? ORDER BY id ASC",
                (run_id, active_epoch),
            ).fetchall()
        # ASC then overwrite -> the highest-id (latest) revision per generation wins.
        latest: dict[int, GenerationScoreRevisionRow] = {}
        for row in rows:
            latest[int(row["generation_index"])] = cast(GenerationScoreRevisionRow, dict(row))
        return latest

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
        winner: str = "",
        strategy_json: str = "",
        replay_json: str = "",
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO matches(
                    run_id, generation_index, seed, score,
                    passed_validation, validation_errors,
                    winner, strategy_json, replay_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    generation_index,
                    seed,
                    score,
                    int(passed_validation),
                    validation_errors,
                    winner or "",
                    strategy_json or "",
                    replay_json or "",
                ),
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

    def get_agent_outputs_by_role(self, run_id: str, role: str) -> list[dict[str, Any]]:
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

    def get_latest_agent_outputs(self, run_id: str) -> dict[str, Any]:
        """Return every agent's output text for the most recent generation of a run.

        Delegates to ``agent_output_queries.latest_agent_outputs`` (extracted to keep this
        module under its size cap). Used by the cowork GUI to show the live candidate.
        """
        from autocontext.storage.agent_output_queries import latest_agent_outputs

        with self.connect() as conn:
            return latest_agent_outputs(conn, run_id)

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
            role_metrics=[
                (
                    role,
                    model,
                    input_tokens,
                    output_tokens,
                    latency_ms,
                    subagent_id,
                    status,
                )
            ],
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

    def get_generation_metrics(self, run_id: str) -> list[GenerationMetricsRow]:
        """Return all generation records for a run, ordered by generation index."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM generations WHERE run_id = ? ORDER BY generation_index",
                (run_id,),
            ).fetchall()
            return cast(list[GenerationMetricsRow], [dict(row) for row in rows])

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
                SELECT
                    generation_index,
                    mean_score,
                    best_score,
                    elo,
                    gate_decision,
                    dimension_summary_json,
                    scoring_backend,
                    rating_uncertainty
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
                raw_dimension_summary = d.pop("dimension_summary_json", None)
                if isinstance(raw_dimension_summary, str) and raw_dimension_summary:
                    try:
                        d["dimension_summary"] = json.loads(raw_dimension_summary)
                    except json.JSONDecodeError:
                        d["dimension_summary"] = {}
                else:
                    d["dimension_summary"] = {}
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

    def get_self_play_strategy_history(self, run_id: str) -> list[dict[str, Any]]:
        """Return prior competitor strategies with Elo for self-play scheduling."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT ao.generation_index, ao.content, g.best_score, g.gate_decision, g.elo
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
        scoring_backend: str = "elo",
        rating_uncertainty: float | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO knowledge_snapshots(
                    scenario, run_id, best_score, best_elo, playbook_hash, agent_provider,
                    rlm_enabled, scoring_backend, rating_uncertainty
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scenario,
                    run_id,
                    best_score,
                    best_elo,
                    playbook_hash,
                    agent_provider,
                    int(rlm_enabled),
                    scoring_backend,
                    rating_uncertainty,
                ),
            )

    def get_best_knowledge_snapshot(self, scenario: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    scenario,
                    run_id,
                    best_score,
                    best_elo,
                    playbook_hash,
                    scoring_backend,
                    rating_uncertainty,
                    created_at
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
                SELECT
                    scenario,
                    run_id,
                    best_score,
                    best_elo,
                    playbook_hash,
                    agent_provider,
                    rlm_enabled,
                    scoring_backend,
                    rating_uncertainty,
                    created_at
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

    def mark_run_stopped(self, run_id: str) -> bool:
        """First-wins stop transition: only a still-running run can be stopped. Returns True if it won the race."""
        with self.connect() as conn:
            cur = conn.execute(
                "UPDATE runs SET status = 'stopped', updated_at = datetime('now') WHERE run_id = ? AND status = 'running'",
                (run_id,),
            )
            return cur.rowcount > 0

    def mark_run_completed(self, run_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE runs SET status = 'completed', updated_at = datetime('now') "
                "WHERE run_id = ? AND (status IS NULL OR status != 'stopped')",
                (run_id,),
            )

    def mark_run_failed(self, run_id: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE runs SET status = 'failed', updated_at = datetime('now') WHERE run_id = ?", (run_id,))

    def mark_run_running(self, run_id: str, target_generations: int | None = None) -> None:
        with self.connect() as conn:
            if target_generations is None:
                conn.execute(
                    "UPDATE runs SET status = 'running', updated_at = datetime('now') WHERE run_id = ?",
                    (run_id,),
                )
            else:
                conn.execute(
                    """
                    UPDATE runs
                    SET status = 'running',
                        target_generations = ?,
                        updated_at = datetime('now')
                    WHERE run_id = ?
                    """,
                    (target_generations, run_id),
                )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """Return a single run row by id."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            return dict(row) if row else None

    # -- Shared query services (AC-480) --
    # These replace duplicated raw SQL in cli.py, mcp/tools.py, and server/ endpoints.

    def list_runs(self, *, limit: int = 50) -> list[RunRow]:
        """List recent runs, newest first."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT run_id, scenario, target_generations, executor_mode, status, created_at "
                "FROM runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return cast(list[RunRow], [dict(row) for row in rows])

    def run_status(self, run_id: str) -> list[dict[str, Any]]:
        """Return per-generation status for a run."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT generation_index, mean_score, best_score, elo, wins, losses, gate_decision, status,
                       evaluator_epoch, quarantined
                FROM generations
                WHERE run_id = ?
                ORDER BY generation_index
                """,
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_solved(self) -> list[dict[str, Any]]:
        """Return best knowledge snapshots per scenario."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT scenario, best_score, best_elo, run_id, created_at FROM knowledge_snapshots ORDER BY best_score DESC"
            ).fetchall()
        # Deduplicate: keep only the best per scenario
        seen: dict[str, dict[str, Any]] = {}
        for row in rows:
            d = dict(row)
            scn = d["scenario"]
            if scn not in seen or d["best_score"] > seen[scn]["best_score"]:
                seen[scn] = d
        return list(seen.values())

    # ---- Shared JSON field parsing (used by hub + notebook mixins) ----

    @staticmethod
    def _parse_json_field(raw: Any, default: Any) -> Any:
        if not isinstance(raw, str):
            return default
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return default

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
