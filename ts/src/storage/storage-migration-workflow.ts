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
  "012_research_hub.sql": ["012_research_hub.sql"],
  "014_generation_evaluator_epoch.sql": ["016_generation_evaluator_epoch.sql"],
  "015_generation_quarantined.sql": ["017_generation_quarantined.sql"],
  "016_generation_score_revisions.sql": ["018_generation_score_revisions.sql"],
  "017_task_queue_attempts.sql": ["019_task_queue_attempts.sql"],
  "019_run_minimum_generations.sql": ["020_run_minimum_generations.sql"],
  "020_human_feedback_acquisition.sql": ["021_human_feedback_acquisition.sql"],
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

type AddedColumn = {
  readonly table: string;
  readonly column: string;
  readonly definition: string;
};

type PreservedRebuildColumn = AddedColumn & {
  readonly key: string;
};

const RUNS_MINIMUM_GENERATIONS: AddedColumn = {
  table: "runs",
  column: "minimum_generations",
  definition: "INTEGER NOT NULL DEFAULT 1 CHECK (minimum_generations >= 1)",
};

// Table rebuilds copy a fixed column list, so they would drop columns that a
// runtime added before the rebuild ran (for example Python 020 before TS 013).
const TYPESCRIPT_REBUILD_PRESERVED_COLUMNS: Record<string, readonly PreservedRebuildColumn[]> = {
  "013_runs_status_default_parity.sql": [{ ...RUNS_MINIMUM_GENERATIONS, key: "run_id" }],
};

// Migrations whose only effect is adding these columns. A Python bootstrap
// creates the current schema, and before it recorded Python 020 the column
// already existed while 019 still looked pending.
const TYPESCRIPT_COLUMN_ONLY_MIGRATIONS: Record<string, readonly AddedColumn[]> = {
  "019_run_minimum_generations.sql": [RUNS_MINIMUM_GENERATIONS],
};

function readAppliedSet(
  db: Database.Database,
  sql: string,
  column: "filename" | "version",
): Set<string> {
  return new Set(
    (db.prepare(sql).all() as Array<Record<typeof column, string>>).map((row) => row[column]),
  );
}

function isCoveredByPythonLedger(file: string, appliedPython: Set<string>): boolean {
  const pythonBaselines = TYPESCRIPT_TO_PYTHON_MIGRATION_BASELINES[file] ?? [];
  return (
    pythonBaselines.length > 0 && pythonBaselines.every((migration) => appliedPython.has(migration))
  );
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

function hasColumn(db: Database.Database, table: string, column: string): boolean {
  return (db.prepare(`PRAGMA table_info(${table})`).all() as Array<{ name: string }>).some(
    (row) => row.name === column,
  );
}

function hasEveryAddedColumn(db: Database.Database, file: string): boolean {
  const columns = TYPESCRIPT_COLUMN_ONLY_MIGRATIONS[file] ?? [];
  return columns.length > 0 && columns.every(({ table, column }) => hasColumn(db, table, column));
}

function preservedColumnStash({ table, column }: PreservedRebuildColumn): string {
  return `preserved_${table}_${column}`;
}

function execPreservingRebuildColumns(db: Database.Database, file: string, sql: string): void {
  const preserved = (TYPESCRIPT_REBUILD_PRESERVED_COLUMNS[file] ?? []).filter(({ table, column }) =>
    hasColumn(db, table, column),
  );
  for (const entry of preserved) {
    db.exec(
      `CREATE TEMP TABLE ${preservedColumnStash(entry)} AS
         SELECT ${entry.key}, ${entry.column} FROM ${entry.table}`,
    );
  }
  db.exec(sql);
  for (const entry of preserved) {
    const { table, key, column, definition } = entry;
    const stash = preservedColumnStash(entry);
    db.exec(
      `ALTER TABLE ${table} ADD COLUMN ${column} ${definition};
       UPDATE ${table} SET ${column} = stash.${column}
         FROM ${stash} AS stash
        WHERE stash.${key} = ${table}.${key};
       DROP TABLE ${stash};`,
    );
  }
}

export function migrateDatabase(db: Database.Database, migrationsDir: string): void {
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
  const pending = readdirSync(migrationsDir)
    .filter((file) => file.endsWith(".sql") && !appliedTypescript.has(file))
    .sort();

  if (pending.length === 0) {
    return;
  }

  // PRAGMA foreign_keys is a no-op inside a transaction, so table rebuilds
  // such as 013 need enforcement off before BEGIN or DROP TABLE cascades.
  const foreignKeys = db.pragma("foreign_keys", { simple: true });
  db.pragma("foreign_keys = OFF");
  try {
    for (const file of pending) {
      db.transaction(() => applyPendingMigration(db, migrationsDir, file)).immediate();
    }
  } finally {
    db.pragma(`foreign_keys = ${foreignKeys ? "ON" : "OFF"}`);
  }
}

function applyPendingMigration(db: Database.Database, migrationsDir: string, file: string): void {
  // Another process may be migrating the same database. This runs under the
  // write lock, so re-read both ledgers before applying the file.
  const appliedTypescript = readAppliedSet(db, "SELECT filename FROM schema_version", "filename");
  const appliedPython = readAppliedSet(db, "SELECT version FROM schema_migrations", "version");
  if (appliedTypescript.has(file)) {
    return;
  }
  if (isCoveredByPythonLedger(file, appliedPython)) {
    db.prepare("INSERT OR IGNORE INTO schema_version(filename) VALUES (?)").run(file);
    return;
  }
  if (!hasEveryAddedColumn(db, file)) {
    const sql = readFileSync(join(migrationsDir, file), "utf8");
    execPreservingRebuildColumns(db, file, sql);
  }
  reconcilePythonBaselineSchema(db, file);
  db.prepare("INSERT INTO schema_version(filename) VALUES (?)").run(file);
  for (const pythonMigration of TYPESCRIPT_TO_PYTHON_MIGRATION_BASELINES[file] ?? []) {
    db.prepare("INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)").run(pythonMigration);
  }
}
