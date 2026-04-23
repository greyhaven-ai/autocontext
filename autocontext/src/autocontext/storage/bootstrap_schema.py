from __future__ import annotations

import sqlite3
from pathlib import Path

from autocontext.storage.migration_ledgers import TYPESCRIPT_BASELINE_MIGRATIONS

_BOOTSTRAP_MIGRATIONS = (
    "001_initial.sql",
    "002_phase3_phase7.sql",
    "003_agent_subagent_metadata.sql",
    "004_knowledge_inheritance.sql",
    "005_ecosystem_provider_tracking.sql",
    "006_human_feedback.sql",
    "007_task_queue.sql",
    "008_staged_validation.sql",
    "009_generation_timing.sql",
    "010_consultation_log.sql",
    "010_session_notebook.sql",
    "011_monitors.sql",
    "012_research_hub.sql",
    "013_generation_dimension_summary.sql",
    "014_scoring_backend_metadata.sql",
    "015_match_replay.sql",
)


def default_migrations_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "migrations"


def bootstrap_core_schema(conn: sqlite3.Connection) -> None:
    """Create the current storage schema when SQL migration files are unavailable."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            filename TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            scenario TEXT NOT NULL,
            target_generations INTEGER NOT NULL,
            executor_mode TEXT NOT NULL,
            status TEXT NOT NULL,
            agent_provider TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS generations (
            run_id TEXT NOT NULL,
            generation_index INTEGER NOT NULL,
            mean_score REAL NOT NULL,
            best_score REAL NOT NULL,
            gate_decision TEXT NOT NULL,
            status TEXT NOT NULL,
            elo REAL NOT NULL DEFAULT 1000.0,
            wins INTEGER NOT NULL DEFAULT 0,
            losses INTEGER NOT NULL DEFAULT 0,
            duration_seconds REAL DEFAULT NULL,
            dimension_summary_json TEXT DEFAULT NULL,
            scoring_backend TEXT NOT NULL DEFAULT 'elo',
            rating_uncertainty REAL DEFAULT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (run_id, generation_index),
            FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            generation_index INTEGER NOT NULL,
            seed INTEGER NOT NULL,
            score REAL NOT NULL,
            passed_validation INTEGER NOT NULL,
            validation_errors TEXT NOT NULL DEFAULT '',
            winner TEXT NOT NULL DEFAULT '',
            strategy_json TEXT NOT NULL DEFAULT '',
            replay_json TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (run_id, generation_index)
                REFERENCES generations(run_id, generation_index) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS agent_outputs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            generation_index INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (run_id, generation_index)
                REFERENCES generations(run_id, generation_index) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS generation_recovery (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            generation_index INTEGER NOT NULL,
            decision TEXT NOT NULL,
            reason TEXT NOT NULL,
            retry_count INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (run_id, generation_index)
                REFERENCES generations(run_id, generation_index) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS agent_role_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            generation_index INTEGER NOT NULL,
            role TEXT NOT NULL,
            model TEXT NOT NULL,
            input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL,
            latency_ms INTEGER NOT NULL,
            subagent_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'completed',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (run_id, generation_index)
                REFERENCES generations(run_id, generation_index) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS knowledge_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scenario TEXT NOT NULL,
            run_id TEXT NOT NULL,
            best_score REAL NOT NULL,
            best_elo REAL NOT NULL,
            playbook_hash TEXT NOT NULL,
            agent_provider TEXT NOT NULL DEFAULT '',
            rlm_enabled INTEGER NOT NULL DEFAULT 0,
            scoring_backend TEXT NOT NULL DEFAULT 'elo',
            rating_uncertainty REAL DEFAULT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_knowledge_snapshots_scenario
            ON knowledge_snapshots(scenario, best_score DESC);

        CREATE TABLE IF NOT EXISTS human_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scenario_name TEXT NOT NULL,
            generation_id TEXT,
            agent_output TEXT NOT NULL,
            human_score REAL,
            human_notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_feedback_scenario ON human_feedback(scenario_name);

        CREATE TABLE IF NOT EXISTS task_queue (
            id TEXT PRIMARY KEY,
            spec_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            priority INTEGER NOT NULL DEFAULT 0,
            config_json TEXT,
            scheduled_at TEXT,
            started_at TEXT,
            completed_at TEXT,
            best_score REAL,
            best_output TEXT,
            total_rounds INTEGER,
            met_threshold INTEGER DEFAULT 0,
            result_json TEXT,
            error TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_task_queue_status ON task_queue(status);
        CREATE INDEX IF NOT EXISTS idx_task_queue_priority ON task_queue(priority DESC, created_at ASC);
        CREATE INDEX IF NOT EXISTS idx_task_queue_spec ON task_queue(spec_name);

        CREATE TABLE IF NOT EXISTS staged_validation_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            generation_index INTEGER NOT NULL,
            stage_order INTEGER NOT NULL,
            stage_name TEXT NOT NULL,
            status TEXT NOT NULL,
            duration_ms REAL NOT NULL,
            error TEXT,
            error_code TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (run_id, generation_index)
                REFERENCES generations(run_id, generation_index) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS consultation_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            generation_index INTEGER NOT NULL,
            trigger TEXT NOT NULL,
            context_summary TEXT NOT NULL DEFAULT '',
            critique TEXT NOT NULL DEFAULT '',
            alternative_hypothesis TEXT NOT NULL DEFAULT '',
            tiebreak_recommendation TEXT NOT NULL DEFAULT '',
            suggested_next_action TEXT NOT NULL DEFAULT '',
            raw_response TEXT NOT NULL DEFAULT '',
            model_used TEXT NOT NULL DEFAULT '',
            cost_usd REAL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            FOREIGN KEY (run_id) REFERENCES runs(run_id)
        );
        CREATE INDEX IF NOT EXISTS idx_consultation_log_run ON consultation_log(run_id);

        CREATE TABLE IF NOT EXISTS session_notebooks (
            session_id TEXT PRIMARY KEY,
            scenario_name TEXT NOT NULL,
            current_objective TEXT NOT NULL DEFAULT '',
            current_hypotheses TEXT NOT NULL DEFAULT '[]',
            best_run_id TEXT,
            best_generation INTEGER,
            best_score REAL,
            unresolved_questions TEXT NOT NULL DEFAULT '[]',
            operator_observations TEXT NOT NULL DEFAULT '[]',
            follow_ups TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );
        CREATE INDEX IF NOT EXISTS idx_session_notebooks_scenario ON session_notebooks(scenario_name);

        CREATE TABLE IF NOT EXISTS monitor_conditions (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            condition_type TEXT NOT NULL,
            params_json TEXT NOT NULL DEFAULT '{}',
            scope TEXT NOT NULL DEFAULT 'global',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );

        CREATE TABLE IF NOT EXISTS monitor_alerts (
            id TEXT PRIMARY KEY,
            condition_id TEXT NOT NULL,
            condition_name TEXT NOT NULL,
            condition_type TEXT NOT NULL,
            scope TEXT NOT NULL DEFAULT 'global',
            detail TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL DEFAULT '{}',
            fired_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            FOREIGN KEY (condition_id) REFERENCES monitor_conditions(id)
        );
        CREATE INDEX IF NOT EXISTS idx_monitor_conditions_active ON monitor_conditions(active);
        CREATE INDEX IF NOT EXISTS idx_monitor_alerts_condition ON monitor_alerts(condition_id);
        CREATE INDEX IF NOT EXISTS idx_monitor_alerts_fired_at ON monitor_alerts(fired_at DESC);

        CREATE TABLE IF NOT EXISTS hub_sessions (
            session_id TEXT PRIMARY KEY,
            owner TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            lease_expires_at TEXT NOT NULL DEFAULT '',
            last_heartbeat_at TEXT NOT NULL DEFAULT '',
            shared INTEGER NOT NULL DEFAULT 0,
            external_link TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            FOREIGN KEY (session_id) REFERENCES session_notebooks(session_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_hub_sessions_status ON hub_sessions(status);
        CREATE INDEX IF NOT EXISTS idx_hub_sessions_shared ON hub_sessions(shared);
        CREATE INDEX IF NOT EXISTS idx_hub_sessions_heartbeat ON hub_sessions(last_heartbeat_at DESC);

        CREATE TABLE IF NOT EXISTS hub_packages (
            package_id TEXT PRIMARY KEY,
            scenario_name TEXT NOT NULL,
            scenario_family TEXT NOT NULL DEFAULT '',
            source_run_id TEXT NOT NULL DEFAULT '',
            source_generation INTEGER NOT NULL DEFAULT 0,
            title TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            promotion_level TEXT NOT NULL DEFAULT 'experimental',
            best_score REAL NOT NULL DEFAULT 0.0,
            best_elo REAL NOT NULL DEFAULT 0.0,
            payload_path TEXT NOT NULL DEFAULT '',
            strategy_package_path TEXT NOT NULL DEFAULT '',
            tags_json TEXT NOT NULL DEFAULT '[]',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );
        CREATE INDEX IF NOT EXISTS idx_hub_packages_scenario ON hub_packages(scenario_name);
        CREATE INDEX IF NOT EXISTS idx_hub_packages_family ON hub_packages(scenario_family);
        CREATE INDEX IF NOT EXISTS idx_hub_packages_source_run ON hub_packages(source_run_id);
        CREATE INDEX IF NOT EXISTS idx_hub_packages_created_at ON hub_packages(created_at DESC);

        CREATE TABLE IF NOT EXISTS hub_results (
            result_id TEXT PRIMARY KEY,
            scenario_name TEXT NOT NULL,
            run_id TEXT NOT NULL DEFAULT '',
            package_id TEXT,
            title TEXT NOT NULL DEFAULT '',
            best_score REAL NOT NULL DEFAULT 0.0,
            best_elo REAL NOT NULL DEFAULT 0.0,
            payload_path TEXT NOT NULL DEFAULT '',
            tags_json TEXT NOT NULL DEFAULT '[]',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );
        CREATE INDEX IF NOT EXISTS idx_hub_results_scenario ON hub_results(scenario_name);
        CREATE INDEX IF NOT EXISTS idx_hub_results_run ON hub_results(run_id);
        CREATE INDEX IF NOT EXISTS idx_hub_results_package ON hub_results(package_id);
        CREATE INDEX IF NOT EXISTS idx_hub_results_created_at ON hub_results(created_at DESC);

        CREATE TABLE IF NOT EXISTS hub_promotions (
            event_id TEXT PRIMARY KEY,
            package_id TEXT NOT NULL DEFAULT '',
            source_run_id TEXT NOT NULL DEFAULT '',
            action TEXT NOT NULL DEFAULT '',
            actor TEXT NOT NULL DEFAULT '',
            label TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );
        CREATE INDEX IF NOT EXISTS idx_hub_promotions_package ON hub_promotions(package_id);
        CREATE INDEX IF NOT EXISTS idx_hub_promotions_source_run ON hub_promotions(source_run_id);
        CREATE INDEX IF NOT EXISTS idx_hub_promotions_created_at ON hub_promotions(created_at DESC);
        """
    )
    conn.executemany(
        "INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)",
        [(version,) for version in _BOOTSTRAP_MIGRATIONS],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO schema_version(filename) VALUES (?)",
        [(version,) for version in TYPESCRIPT_BASELINE_MIGRATIONS],
    )
