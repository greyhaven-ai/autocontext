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
