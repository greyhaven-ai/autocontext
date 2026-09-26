import Database from "better-sqlite3";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { copyFileSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

import {
  migrateDatabase,
  TYPESCRIPT_TO_PYTHON_MIGRATION_BASELINES,
} from "../src/storage/storage-migration-workflow.js";

const MIGRATIONS_DIR = join(import.meta.dirname, "..", "migrations");
const PYTHON_MIGRATIONS_DIR = join(import.meta.dirname, "..", "..", "autocontext", "migrations");

function columnNames(db: Database.Database, tableName: string): Set<string> {
  return new Set(
    (db.prepare(`PRAGMA table_info(${tableName})`).all() as Array<{ name: string }>).map(
      (row) => row.name,
    ),
  );
}

function columnDefault(
  db: Database.Database,
  tableName: string,
  columnName: string,
): string | null {
  const row = (
    db.prepare(`PRAGMA table_info(${tableName})`).all() as Array<{
      dflt_value: string | null;
      name: string;
    }>
  ).find((column) => column.name === columnName);
  if (!row) {
    throw new Error(`missing column ${tableName}.${columnName}`);
  }
  return row.dflt_value;
}

describe("storage migration workflow", () => {
  let dir: string;
  let db: Database.Database;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "ac-storage-migration-"));
    db = new Database(join(dir, "test.db"));
  });

  afterEach(() => {
    db.close();
    rmSync(dir, { recursive: true, force: true });
  });

  it("applies migrations idempotently with schema version tracking", () => {
    migrateDatabase(db, MIGRATIONS_DIR);
    migrateDatabase(db, MIGRATIONS_DIR);

    const versions = db
      .prepare("SELECT filename FROM schema_version ORDER BY filename")
      .all() as Array<{ filename: string }>;
    expect(versions.length).toBeGreaterThan(0);
    expect(new Set(versions.map((row) => row.filename)).size).toBe(versions.length);
    expect(Array.from(columnNames(db, "runs"))).toContain("minimum_generations");
    expect(columnDefault(db, "runs", "minimum_generations")).toBe("1");
  });

  it("seeds the Python migration ledger for shared TypeScript baselines", () => {
    migrateDatabase(db, MIGRATIONS_DIR);

    const appliedPython = new Set(
      (db.prepare("SELECT version FROM schema_migrations").all() as Array<{ version: string }>).map(
        (row) => row.version,
      ),
    );
    for (const pythonMigration of Object.values(TYPESCRIPT_TO_PYTHON_MIGRATION_BASELINES).flat()) {
      expect(appliedPython.has(pythonMigration)).toBe(true);
    }
  });

  function applyEveryPythonMigration(target: Database.Database): void {
    target.exec(
      `CREATE TABLE schema_migrations (
         version TEXT PRIMARY KEY,
         applied_at TEXT NOT NULL DEFAULT (datetime('now'))
       )`,
    );
    const insert = target.prepare("INSERT INTO schema_migrations(version) VALUES (?)");
    const pythonFiles = readdirSync(PYTHON_MIGRATIONS_DIR)
      .filter((file) => file.endsWith(".sql"))
      .sort();
    for (const pythonMigration of pythonFiles) {
      target.exec(readFileSync(join(PYTHON_MIGRATIONS_DIR, pythonMigration), "utf8"));
      insert.run(pythonMigration);
    }
  }

  function applyPendingPythonMigrations(target: Database.Database): void {
    const appliedPython = new Set(
      (
        target.prepare("SELECT version FROM schema_migrations").all() as Array<{ version: string }>
      ).map((row) => row.version),
    );
    const insert = target.prepare("INSERT INTO schema_migrations(version) VALUES (?)");
    const pythonFiles = readdirSync(PYTHON_MIGRATIONS_DIR)
      .filter((file) => file.endsWith(".sql") && !appliedPython.has(file))
      .sort();
    for (const pythonMigration of pythonFiles) {
      target.exec(readFileSync(join(PYTHON_MIGRATIONS_DIR, pythonMigration), "utf8"));
      insert.run(pythonMigration);
    }
  }

  function insertRunWithGeneration(target: Database.Database, minimumGenerations: number): void {
    target
      .prepare(
        `INSERT INTO runs(
           run_id,
           scenario,
           minimum_generations,
           target_generations,
           executor_mode,
           status,
           agent_provider
         )
         VALUES ('run-1', 'grid_ctf', ?, 5, 'local', 'running', 'claude')`,
      )
      .run(minimumGenerations);
    target.exec(
      `INSERT INTO generations(
         run_id,
         generation_index,
         mean_score,
         best_score,
         gate_decision,
         status
       )
       VALUES ('run-1', 0, 0.5, 0.6, 'advance', 'completed')`,
    );
  }

  function expectRunsMinimumGenerationsPreserved(target: Database.Database): void {
    expect(Array.from(columnNames(target, "runs"))).toContain("minimum_generations");
    expect(columnDefault(target, "runs", "minimum_generations")).toBe("1");
    expect(
      target
        .prepare("SELECT minimum_generations, agent_provider FROM runs WHERE run_id = ?")
        .get("run-1"),
    ).toEqual({ agent_provider: "claude", minimum_generations: 3 });
    expect(
      target.prepare("SELECT COUNT(*) AS count FROM generations WHERE run_id = ?").get("run-1"),
    ).toEqual({ count: 1 });
    expect(() =>
      target
        .prepare(
          `INSERT INTO runs(
             run_id,
             scenario,
             minimum_generations,
             target_generations,
             executor_mode,
             status
           )
           VALUES ('run-2', 'grid_ctf', 0, 5, 'local', 'running')`,
        )
        .run(),
    ).toThrow(/CHECK constraint failed/);
  }

  it("keeps runs.minimum_generations when migration 013 rebuilds a Python-owned runs table", () => {
    db.pragma("foreign_keys = ON");
    applyEveryPythonMigration(db);
    insertRunWithGeneration(db, 3);
    const pythonRunColumns = columnNames(db, "runs");

    migrateDatabase(db, MIGRATIONS_DIR);

    expect(columnNames(db, "runs")).toEqual(pythonRunColumns);
    expectRunsMinimumGenerationsPreserved(db);
    expect(
      db
        .prepare("SELECT filename FROM schema_version WHERE filename = ?")
        .get("019_run_minimum_generations.sql"),
    ).toEqual({ filename: "019_run_minimum_generations.sql" });
  });

  it("keeps runs.minimum_generations that Python added before TypeScript applied migration 013", () => {
    db.pragma("foreign_keys = ON");
    const pre013MigrationsDir = join(dir, "pre-013-migrations");
    mkdirSync(pre013MigrationsDir);
    for (const file of readdirSync(MIGRATIONS_DIR).filter(
      (name) => name.endsWith(".sql") && name < "013_",
    )) {
      copyFileSync(join(MIGRATIONS_DIR, file), join(pre013MigrationsDir, file));
    }
    migrateDatabase(db, pre013MigrationsDir);
    expect(columnDefault(db, "runs", "status")).toBe("'running'");
    applyPendingPythonMigrations(db);
    insertRunWithGeneration(db, 3);

    migrateDatabase(db, MIGRATIONS_DIR);

    expect(columnDefault(db, "runs", "status")).toBeNull();
    expectRunsMinimumGenerationsPreserved(db);
  });

  it("records 019 without re-adding runs.minimum_generations that a Python bootstrap created", () => {
    db.pragma("foreign_keys = ON");
    applyEveryPythonMigration(db);
    // Python bootstraps the schema when migration files are missing (pip
    // installs), and its bootstrap created this column without recording 020.
    db.prepare("DELETE FROM schema_migrations WHERE version = ?").run(
      "020_run_minimum_generations.sql",
    );
    insertRunWithGeneration(db, 3);

    migrateDatabase(db, MIGRATIONS_DIR);

    expectRunsMinimumGenerationsPreserved(db);
    expect(
      db
        .prepare("SELECT filename FROM schema_version WHERE filename = ?")
        .get("019_run_minimum_generations.sql"),
    ).toEqual({ filename: "019_run_minimum_generations.sql" });
    expect(
      db
        .prepare("SELECT version FROM schema_migrations WHERE version = ?")
        .get("020_run_minimum_generations.sql"),
    ).toEqual({ version: "020_run_minimum_generations.sql" });
  });

  it("restores runs.minimum_generations when both ledgers record it but an earlier 013 dropped it", () => {
    db.pragma("foreign_keys = ON");
    applyEveryPythonMigration(db);
    insertRunWithGeneration(db, 3);
    migrateDatabase(db, MIGRATIONS_DIR);
    // Leave the database as the runner did before 013 preserved the column:
    // 013 and 019 recorded, 020 recorded, and the column gone.
    db.exec("ALTER TABLE runs DROP COLUMN minimum_generations");

    migrateDatabase(db, MIGRATIONS_DIR);
    migrateDatabase(db, MIGRATIONS_DIR);

    expect(
      (db.prepare("PRAGMA table_info(runs)").all() as Array<{ name: string }>).filter(
        (column) => column.name === "minimum_generations",
      ),
    ).toHaveLength(1);
    expect(columnDefault(db, "runs", "minimum_generations")).toBe("1");
    expect(
      db.prepare("SELECT minimum_generations FROM runs WHERE run_id = ?").get("run-1"),
    ).toEqual({ minimum_generations: 1 });
    expect(
      db.prepare("SELECT COUNT(*) AS count FROM generations WHERE run_id = ?").get("run-1"),
    ).toEqual({ count: 1 });
    expect(() =>
      db
        .prepare(
          `INSERT INTO runs(
             run_id,
             scenario,
             minimum_generations,
             target_generations,
             executor_mode,
             status
           )
           VALUES ('run-2', 'grid_ctf', 0, 5, 'local', 'running')`,
        )
        .run(),
    ).toThrow(/CHECK constraint failed/);
  });

  it("leaves runs.minimum_generations to migration 019 until a ledger records it", () => {
    const pre019MigrationsDir = join(dir, "pre-019-migrations");
    mkdirSync(pre019MigrationsDir);
    for (const file of readdirSync(MIGRATIONS_DIR).filter(
      (name) => name.endsWith(".sql") && name < "019_",
    )) {
      copyFileSync(join(MIGRATIONS_DIR, file), join(pre019MigrationsDir, file));
    }

    migrateDatabase(db, pre019MigrationsDir);

    expect(Array.from(columnNames(db, "runs"))).not.toContain("minimum_generations");
    expect(() => migrateDatabase(db, MIGRATIONS_DIR)).not.toThrow();
    expect(columnDefault(db, "runs", "minimum_generations")).toBe("1");
  });

  it("migrates a fully Python-owned database without duplicating the evaluator epoch column", () => {
    applyEveryPythonMigration(db);
    expect(
      db
        .prepare("SELECT version FROM schema_migrations WHERE version = ?")
        .get("016_generation_evaluator_epoch.sql"),
    ).toEqual({ version: "016_generation_evaluator_epoch.sql" });

    expect(() => migrateDatabase(db, MIGRATIONS_DIR)).not.toThrow();

    const evaluatorEpochColumns = (
      db.prepare("PRAGMA table_info(generations)").all() as Array<{ name: string }>
    ).filter((column) => column.name === "evaluator_epoch");
    expect(evaluatorEpochColumns).toHaveLength(1);
    expect(
      db
        .prepare("SELECT filename FROM schema_version WHERE filename = ?")
        .get("014_generation_evaluator_epoch.sql"),
    ).toEqual({ filename: "014_generation_evaluator_epoch.sql" });

    const quarantinedColumns = (
      db.prepare("PRAGMA table_info(generations)").all() as Array<{ name: string }>
    ).filter((column) => column.name === "quarantined");
    expect(quarantinedColumns).toHaveLength(1);
    expect(
      db
        .prepare("SELECT filename FROM schema_version WHERE filename = ?")
        .get("015_generation_quarantined.sql"),
    ).toEqual({ filename: "015_generation_quarantined.sql" });
  });

  it("migrates a fully Python-owned database without duplicating the score revisions table", () => {
    applyEveryPythonMigration(db);
    expect(
      db
        .prepare("SELECT version FROM schema_migrations WHERE version = ?")
        .get("018_generation_score_revisions.sql"),
    ).toEqual({ version: "018_generation_score_revisions.sql" });

    expect(() => migrateDatabase(db, MIGRATIONS_DIR)).not.toThrow();

    const scoreRevisionTables = db
      .prepare("SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?")
      .all("generation_score_revisions") as Array<{ name: string }>;
    expect(scoreRevisionTables).toHaveLength(1);
    expect(
      db
        .prepare("SELECT filename FROM schema_version WHERE filename = ?")
        .get("016_generation_score_revisions.sql"),
    ).toEqual({ filename: "016_generation_score_revisions.sql" });
  });

  it("creates the score revisions table exactly once on a fresh TypeScript migrate", () => {
    migrateDatabase(db, MIGRATIONS_DIR);

    const scoreRevisionTables = db
      .prepare("SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?")
      .all("generation_score_revisions") as Array<{ name: string }>;
    expect(scoreRevisionTables).toHaveLength(1);
    expect(
      db
        .prepare("SELECT version FROM schema_migrations WHERE version = ?")
        .get("018_generation_score_revisions.sql"),
    ).toEqual({ version: "018_generation_score_revisions.sql" });
  });

  it("marks TypeScript migrations applied when Python already owns the equivalent schema", () => {
    db.exec(
      `CREATE TABLE schema_migrations (
         version TEXT PRIMARY KEY,
         applied_at TEXT NOT NULL DEFAULT (datetime('now'))
       )`,
    );
    const pythonMigrations = [
      ...new Set(Object.values(TYPESCRIPT_TO_PYTHON_MIGRATION_BASELINES).flat()),
    ].sort();
    const insert = db.prepare("INSERT INTO schema_migrations(version) VALUES (?)");
    for (const pythonMigration of pythonMigrations) {
      db.exec(readFileSync(join(PYTHON_MIGRATIONS_DIR, pythonMigration), "utf8"));
      insert.run(pythonMigration);
    }

    migrateDatabase(db, MIGRATIONS_DIR);

    const appliedTypescript = new Set(
      (db.prepare("SELECT filename FROM schema_version").all() as Array<{ filename: string }>).map(
        (row) => row.filename,
      ),
    );
    for (const typescriptMigration of Object.keys(TYPESCRIPT_TO_PYTHON_MIGRATION_BASELINES)) {
      expect(appliedTypescript.has(typescriptMigration)).toBe(true);
    }
  });

  it("reconciles partial Python baselines before seeding their ledger rows", () => {
    db.exec(
      `CREATE TABLE schema_migrations (
         version TEXT PRIMARY KEY,
         applied_at TEXT NOT NULL DEFAULT (datetime('now'))
       )`,
    );
    const insert = db.prepare("INSERT INTO schema_migrations(version) VALUES (?)");
    for (const pythonMigration of [
      "001_initial.sql",
      "002_phase3_phase7.sql",
      "003_agent_subagent_metadata.sql",
      "004_knowledge_inheritance.sql",
      "005_ecosystem_provider_tracking.sql",
    ]) {
      db.exec(readFileSync(join(PYTHON_MIGRATIONS_DIR, pythonMigration), "utf8"));
      insert.run(pythonMigration);
    }

    migrateDatabase(db, MIGRATIONS_DIR);

    expect(Array.from(columnNames(db, "generations"))).toEqual(
      expect.arrayContaining([
        "duration_seconds",
        "dimension_summary_json",
        "scoring_backend",
        "rating_uncertainty",
      ]),
    );
    expect(Array.from(columnNames(db, "matches"))).toEqual(
      expect.arrayContaining(["winner", "strategy_json", "replay_json"]),
    );

    const appliedPython = new Set(
      (db.prepare("SELECT version FROM schema_migrations").all() as Array<{ version: string }>).map(
        (row) => row.version,
      ),
    );
    for (const pythonMigration of TYPESCRIPT_TO_PYTHON_MIGRATION_BASELINES[
      "009_generation_loop.sql"
    ]) {
      expect(appliedPython.has(pythonMigration)).toBe(true);
    }
  });

  it("removes the historical runs.status default from existing TypeScript databases", () => {
    db.exec(
      `CREATE TABLE runs (
         run_id TEXT PRIMARY KEY,
         scenario TEXT NOT NULL,
         target_generations INTEGER NOT NULL,
         executor_mode TEXT NOT NULL,
         status TEXT NOT NULL DEFAULT 'running',
         agent_provider TEXT NOT NULL DEFAULT '',
         created_at TEXT NOT NULL DEFAULT (datetime('now')),
         updated_at TEXT NOT NULL DEFAULT (datetime('now'))
       );
       INSERT INTO runs(
         run_id,
         scenario,
         target_generations,
         executor_mode,
         status,
         agent_provider,
         created_at,
         updated_at
       )
       VALUES (
         'run-1',
         'grid_ctf',
         2,
         'codex',
         'queued',
         'claude',
         '2026-04-25T00:00:00.000Z',
         '2026-04-25T00:00:01.000Z'
       );
       CREATE TABLE generations (
         generation_id TEXT PRIMARY KEY
       );
       CREATE TABLE schema_version (
         filename TEXT PRIMARY KEY,
         applied_at TEXT NOT NULL DEFAULT (datetime('now'))
       );
       INSERT INTO schema_version(filename) VALUES ('009_generation_loop.sql');`,
    );

    migrateDatabase(db, MIGRATIONS_DIR);

    expect(columnDefault(db, "runs", "status")).toBeNull();
    expect(
      db.prepare("SELECT status, agent_provider FROM runs WHERE run_id = ?").get("run-1"),
    ).toEqual({
      agent_provider: "claude",
      status: "queued",
    });
    expect(
      db
        .prepare("SELECT filename FROM schema_version WHERE filename = ?")
        .get("013_runs_status_default_parity.sql"),
    ).toEqual({ filename: "013_runs_status_default_parity.sql" });
  });

  it("keeps cascading child rows when rebuilding runs with foreign keys enabled", () => {
    db.pragma("foreign_keys = ON");
    db.exec(
      `CREATE TABLE runs (
         run_id TEXT PRIMARY KEY,
         scenario TEXT NOT NULL,
         target_generations INTEGER NOT NULL,
         executor_mode TEXT NOT NULL,
         status TEXT NOT NULL DEFAULT 'running',
         agent_provider TEXT NOT NULL DEFAULT '',
         created_at TEXT NOT NULL DEFAULT (datetime('now')),
         updated_at TEXT NOT NULL DEFAULT (datetime('now'))
       );
       INSERT INTO runs(run_id, scenario, target_generations, executor_mode, status)
       VALUES ('run-1', 'grid_ctf', 2, 'codex', 'running');
       CREATE TABLE generations (
         run_id TEXT NOT NULL,
         generation_index INTEGER NOT NULL,
         PRIMARY KEY (run_id, generation_index),
         FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
       );
       INSERT INTO generations(run_id, generation_index) VALUES ('run-1', 0);
       CREATE TABLE schema_version (
         filename TEXT PRIMARY KEY,
         applied_at TEXT NOT NULL DEFAULT (datetime('now'))
       );
       INSERT INTO schema_version(filename) VALUES ('009_generation_loop.sql');`,
    );

    migrateDatabase(db, MIGRATIONS_DIR);

    expect(db.prepare("SELECT run_id, generation_index FROM generations").all()).toEqual([
      { generation_index: 0, run_id: "run-1" },
    ]);
    expect(db.pragma("foreign_keys", { simple: true })).toBe(1);
  });
});
