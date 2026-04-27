import type Database from "better-sqlite3";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

export const TYPESCRIPT_TO_PYTHON_MIGRATION_BASELINES: Record<string, readonly string[]> = {
  "007_task_queue.sql": ["007_task_queue.sql"],
  "008_human_feedback.sql": ["006_human_feedback.sql"],
  "009_generation_loop.sql": [
    "001_initial.sql",
    "002_phase3_phase7.sql",
    "003_agent_subagent_metadata.sql",
    "004_knowledge_inheritance.sql",
    "005_ecosystem_provider_tracking.sql",
    "009_generation_timing.sql",
    "013_generation_dimension_summary.sql",
    "014_scoring_backend_metadata.sql",
    "015_match_replay.sql",
  ],
  "010_session_notebook.sql": ["010_session_notebook.sql"],
  "011_monitors.sql": ["011_monitors.sql"],
  "012_consultation_log.sql": ["010_consultation_log.sql"],
};

const TYPESCRIPT_BASELINE_SCHEMA_RECONCILIATION: Record<string, readonly string[]> = {
  "009_generation_loop.sql": [
    "ALTER TABLE generations ADD COLUMN elo REAL NOT NULL DEFAULT 1000.0",
    "ALTER TABLE generations ADD COLUMN wins INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE generations ADD COLUMN losses INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE agent_role_metrics ADD COLUMN subagent_id TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE agent_role_metrics ADD COLUMN status TEXT NOT NULL DEFAULT 'completed'",
    "ALTER TABLE runs ADD COLUMN agent_provider TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE knowledge_snapshots ADD COLUMN agent_provider TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE knowledge_snapshots ADD COLUMN rlm_enabled INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE generations ADD COLUMN duration_seconds REAL DEFAULT NULL",
    "ALTER TABLE generations ADD COLUMN dimension_summary_json TEXT DEFAULT NULL",
    "ALTER TABLE generations ADD COLUMN scoring_backend TEXT NOT NULL DEFAULT 'elo'",
    "ALTER TABLE generations ADD COLUMN rating_uncertainty REAL DEFAULT NULL",
    "ALTER TABLE knowledge_snapshots ADD COLUMN scoring_backend TEXT NOT NULL DEFAULT 'elo'",
    "ALTER TABLE knowledge_snapshots ADD COLUMN rating_uncertainty REAL DEFAULT NULL",
    "ALTER TABLE matches ADD COLUMN winner TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE matches ADD COLUMN strategy_json TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE matches ADD COLUMN replay_json TEXT NOT NULL DEFAULT ''",
  ],
};

function readAppliedSet(
  db: Database.Database,
  sql: string,
  column: "filename" | "version",
): Set<string> {
  return new Set(
    (db.prepare(sql).all() as Array<Record<typeof column, string>>).map(
      (row) => row[column],
    ),
  );
}

function isCoveredByPythonLedger(file: string, appliedPython: Set<string>): boolean {
  const pythonBaselines = TYPESCRIPT_TO_PYTHON_MIGRATION_BASELINES[file] ?? [];
  return pythonBaselines.length > 0 && pythonBaselines.every((migration) => appliedPython.has(migration));
}

function isDuplicateColumnError(error: unknown): boolean {
  return error instanceof Error && error.message.toLowerCase().includes("duplicate column name");
}

function reconcilePythonBaselineSchema(db: Database.Database, file: string): void {
  for (const statement of TYPESCRIPT_BASELINE_SCHEMA_RECONCILIATION[file] ?? []) {
    try {
      db.exec(statement);
    } catch (error: unknown) {
      if (!isDuplicateColumnError(error)) {
        throw error;
      }
    }
  }
}

export function migrateDatabase(
  db: Database.Database,
  migrationsDir: string,
): void {
  db.exec(
    `CREATE TABLE IF NOT EXISTS schema_version (
       filename TEXT PRIMARY KEY,
       applied_at TEXT NOT NULL DEFAULT (datetime('now'))
     )`,
  );
  db.exec(
    `CREATE TABLE IF NOT EXISTS schema_migrations (
       version TEXT PRIMARY KEY,
       applied_at TEXT NOT NULL DEFAULT (datetime('now'))
     )`,
  );

  const appliedTypescript = readAppliedSet(db, "SELECT filename FROM schema_version", "filename");
  const appliedPython = readAppliedSet(db, "SELECT version FROM schema_migrations", "version");

  const files = readdirSync(migrationsDir)
    .filter((file) => file.endsWith(".sql"))
    .sort();

  for (const file of files) {
    if (appliedTypescript.has(file)) {
      continue;
    }
    if (isCoveredByPythonLedger(file, appliedPython)) {
      db.prepare("INSERT OR IGNORE INTO schema_version(filename) VALUES (?)").run(file);
      appliedTypescript.add(file);
      continue;
    }
    const sql = readFileSync(join(migrationsDir, file), "utf8");
    db.exec(sql);
    reconcilePythonBaselineSchema(db, file);
    db.prepare("INSERT INTO schema_version(filename) VALUES (?)").run(file);
    appliedTypescript.add(file);
    for (const pythonMigration of TYPESCRIPT_TO_PYTHON_MIGRATION_BASELINES[file] ?? []) {
      db.prepare("INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)").run(pythonMigration);
      appliedPython.add(pythonMigration);
    }
  }
}
