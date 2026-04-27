import Database from "better-sqlite3";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
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

    const versions = db.prepare("SELECT filename FROM schema_version ORDER BY filename").all() as Array<{ filename: string }>;
    expect(versions.length).toBeGreaterThan(0);
    expect(new Set(versions.map((row) => row.filename)).size).toBe(versions.length);
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

  it("marks TypeScript migrations applied when Python already owns the equivalent schema", () => {
    db.exec(
      `CREATE TABLE schema_migrations (
         version TEXT PRIMARY KEY,
         applied_at TEXT NOT NULL DEFAULT (datetime('now'))
       )`,
    );
    const pythonMigrations = [...new Set(Object.values(TYPESCRIPT_TO_PYTHON_MIGRATION_BASELINES).flat())]
      .sort();
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
    for (const pythonMigration of TYPESCRIPT_TO_PYTHON_MIGRATION_BASELINES["009_generation_loop.sql"]) {
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
      db.prepare("SELECT filename FROM schema_version WHERE filename = ?")
        .get("013_runs_status_default_parity.sql"),
    ).toEqual({ filename: "013_runs_status_default_parity.sql" });
  });
});
