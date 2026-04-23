-- AC-342: Core generation loop tables for TypeScript port.
-- Consolidates Python migrations 001-005, 009, 013-015 into single schema.

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    scenario TEXT NOT NULL,
    target_generations INTEGER NOT NULL,
    executor_mode TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    agent_provider TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS generations (
    run_id TEXT NOT NULL,
    generation_index INTEGER NOT NULL,
    mean_score REAL NOT NULL,
    best_score REAL NOT NULL,
    elo REAL NOT NULL DEFAULT 1000.0,
    wins INTEGER NOT NULL DEFAULT 0,
    losses INTEGER NOT NULL DEFAULT 0,
    gate_decision TEXT NOT NULL,
    status TEXT NOT NULL,
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
    validation_errors TEXT NOT NULL,
    winner TEXT NOT NULL DEFAULT '',
    strategy_json TEXT NOT NULL DEFAULT '',
    replay_json TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (run_id, generation_index) REFERENCES generations(run_id, generation_index) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS agent_outputs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    generation_index INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (run_id, generation_index) REFERENCES generations(run_id, generation_index) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS generation_recovery (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    generation_index INTEGER NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT NOT NULL,
    retry_count INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (run_id, generation_index) REFERENCES generations(run_id, generation_index) ON DELETE CASCADE
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
    FOREIGN KEY (run_id, generation_index) REFERENCES generations(run_id, generation_index) ON DELETE CASCADE
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
