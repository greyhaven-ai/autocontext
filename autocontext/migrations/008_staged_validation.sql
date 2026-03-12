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
