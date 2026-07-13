CREATE TABLE IF NOT EXISTS generation_score_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    generation_index INTEGER NOT NULL,
    revision_epoch TEXT NOT NULL,
    revision_score REAL NOT NULL,
    previous_epoch TEXT,
    previous_score REAL,
    previous_quarantined INTEGER,
    created_by TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (run_id, generation_index)
        REFERENCES generations(run_id, generation_index) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_generation_score_revisions_run_gen
    ON generation_score_revisions(run_id, generation_index);
