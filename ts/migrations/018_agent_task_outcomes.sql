-- Durable TypeScript-only outcome contract for completed structured agent tasks.
CREATE TABLE IF NOT EXISTS agent_task_outcomes (
    run_id TEXT PRIMARY KEY,
    outcome_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);
