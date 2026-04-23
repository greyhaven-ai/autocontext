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
    db.prepare("INSERT INTO schema_version(filename) VALUES (?)").run(file);
    appliedTypescript.add(file);
    for (const pythonMigration of TYPESCRIPT_TO_PYTHON_MIGRATION_BASELINES[file] ?? []) {
      db.prepare("INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)").run(pythonMigration);
      appliedPython.add(pythonMigration);
    }
  }
}
