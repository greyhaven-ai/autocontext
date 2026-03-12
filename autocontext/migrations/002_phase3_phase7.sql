ALTER TABLE generations ADD COLUMN elo REAL NOT NULL DEFAULT 1000.0;
ALTER TABLE generations ADD COLUMN wins INTEGER NOT NULL DEFAULT 0;
ALTER TABLE generations ADD COLUMN losses INTEGER NOT NULL DEFAULT 0;

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
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (run_id, generation_index) REFERENCES generations(run_id, generation_index) ON DELETE CASCADE
);
