CREATE TABLE IF NOT EXISTS knowledge_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario TEXT NOT NULL,
    run_id TEXT NOT NULL,
    best_score REAL NOT NULL,
    best_elo REAL NOT NULL,
    playbook_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_knowledge_snapshots_scenario
    ON knowledge_snapshots(scenario, best_score DESC);
