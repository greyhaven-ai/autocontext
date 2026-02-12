CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    scenario TEXT NOT NULL,
    target_generations INTEGER NOT NULL,
    executor_mode TEXT NOT NULL,
    status TEXT NOT NULL,
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
