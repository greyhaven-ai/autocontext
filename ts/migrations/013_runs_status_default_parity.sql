-- AC-639: Align the TypeScript runs.status column default with Python.
-- SQLite cannot drop a column default in place, so rebuild the table while
-- preserving existing run rows and the agent_provider column added by TS 009.

PRAGMA foreign_keys=off;

CREATE TABLE runs_without_status_default (
    run_id TEXT PRIMARY KEY,
    scenario TEXT NOT NULL,
    target_generations INTEGER NOT NULL,
    executor_mode TEXT NOT NULL,
    status TEXT NOT NULL,
    agent_provider TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT INTO runs_without_status_default (
    run_id,
    scenario,
    target_generations,
    executor_mode,
    status,
    agent_provider,
    created_at,
    updated_at
)
SELECT
    run_id,
    scenario,
    target_generations,
    executor_mode,
    status,
    agent_provider,
    created_at,
    updated_at
FROM runs;

DROP TABLE runs;
ALTER TABLE runs_without_status_default RENAME TO runs;

PRAGMA foreign_keys=on;
