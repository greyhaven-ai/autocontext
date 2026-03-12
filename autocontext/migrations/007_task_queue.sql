-- Task queue for the always-on runner daemon
CREATE TABLE IF NOT EXISTS task_queue (
    id TEXT PRIMARY KEY,
    spec_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending, running, completed, failed
    priority INTEGER NOT NULL DEFAULT 0,
    config_json TEXT,  -- JSON: max_rounds, quality_threshold, reference_context, etc.
    scheduled_at TEXT,
    started_at TEXT,
    completed_at TEXT,
    best_score REAL,
    best_output TEXT,
    total_rounds INTEGER,
    met_threshold INTEGER DEFAULT 0,
    result_json TEXT,  -- Full ImprovementResult serialized
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_task_queue_status ON task_queue(status);
CREATE INDEX IF NOT EXISTS idx_task_queue_priority ON task_queue(priority DESC, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_task_queue_spec ON task_queue(spec_name);
