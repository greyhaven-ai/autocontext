CREATE TABLE IF NOT EXISTS session_notebooks (
    scenario_name TEXT PRIMARY KEY,
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
