-- AC-209: Monitor conditions and alerts
CREATE TABLE IF NOT EXISTS monitor_conditions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    condition_type TEXT NOT NULL,
    params_json TEXT NOT NULL DEFAULT '{}',
    scope TEXT NOT NULL DEFAULT 'global',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS monitor_alerts (
    id TEXT PRIMARY KEY,
    condition_id TEXT NOT NULL,
    condition_name TEXT NOT NULL,
    condition_type TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'global',
    detail TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    fired_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (condition_id) REFERENCES monitor_conditions(id)
);

CREATE INDEX IF NOT EXISTS idx_monitor_conditions_active ON monitor_conditions(active);
CREATE INDEX IF NOT EXISTS idx_monitor_alerts_condition ON monitor_alerts(condition_id);
CREATE INDEX IF NOT EXISTS idx_monitor_alerts_fired_at ON monitor_alerts(fired_at DESC);
