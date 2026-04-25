CREATE TABLE IF NOT EXISTS hub_sessions (
    session_id TEXT PRIMARY KEY,
    owner TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    lease_expires_at TEXT NOT NULL DEFAULT '',
    last_heartbeat_at TEXT NOT NULL DEFAULT '',
    shared INTEGER NOT NULL DEFAULT 0,
    external_link TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (session_id) REFERENCES session_notebooks(session_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_hub_sessions_status ON hub_sessions(status);
CREATE INDEX IF NOT EXISTS idx_hub_sessions_shared ON hub_sessions(shared);
CREATE INDEX IF NOT EXISTS idx_hub_sessions_heartbeat ON hub_sessions(last_heartbeat_at DESC);

CREATE TABLE IF NOT EXISTS hub_packages (
    package_id TEXT PRIMARY KEY,
    scenario_name TEXT NOT NULL,
    scenario_family TEXT NOT NULL DEFAULT '',
    source_run_id TEXT NOT NULL DEFAULT '',
    source_generation INTEGER NOT NULL DEFAULT 0,
    title TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    promotion_level TEXT NOT NULL DEFAULT 'experimental',
    best_score REAL NOT NULL DEFAULT 0.0,
    best_elo REAL NOT NULL DEFAULT 0.0,
    payload_path TEXT NOT NULL DEFAULT '',
    strategy_package_path TEXT NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_hub_packages_scenario ON hub_packages(scenario_name);
CREATE INDEX IF NOT EXISTS idx_hub_packages_family ON hub_packages(scenario_family);
CREATE INDEX IF NOT EXISTS idx_hub_packages_source_run ON hub_packages(source_run_id);
CREATE INDEX IF NOT EXISTS idx_hub_packages_created_at ON hub_packages(created_at DESC);

CREATE TABLE IF NOT EXISTS hub_results (
    result_id TEXT PRIMARY KEY,
    scenario_name TEXT NOT NULL,
    run_id TEXT NOT NULL DEFAULT '',
    package_id TEXT,
    title TEXT NOT NULL DEFAULT '',
    best_score REAL NOT NULL DEFAULT 0.0,
    best_elo REAL NOT NULL DEFAULT 0.0,
    payload_path TEXT NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_hub_results_scenario ON hub_results(scenario_name);
CREATE INDEX IF NOT EXISTS idx_hub_results_run ON hub_results(run_id);
CREATE INDEX IF NOT EXISTS idx_hub_results_package ON hub_results(package_id);
CREATE INDEX IF NOT EXISTS idx_hub_results_created_at ON hub_results(created_at DESC);

CREATE TABLE IF NOT EXISTS hub_promotions (
    event_id TEXT PRIMARY KEY,
    package_id TEXT NOT NULL DEFAULT '',
    source_run_id TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL DEFAULT '',
    actor TEXT NOT NULL DEFAULT '',
    label TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_hub_promotions_package ON hub_promotions(package_id);
CREATE INDEX IF NOT EXISTS idx_hub_promotions_source_run ON hub_promotions(source_run_id);
CREATE INDEX IF NOT EXISTS idx_hub_promotions_created_at ON hub_promotions(created_at DESC);
