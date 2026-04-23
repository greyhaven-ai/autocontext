import Database from "better-sqlite3";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

import {
  migrateDatabase,
  TYPESCRIPT_TO_PYTHON_MIGRATION_BASELINES,
} from "../src/storage/storage-migration-workflow.js";

const MIGRATIONS_DIR = join(import.meta.dirname, "..", "migrations");

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
    const insert = db.prepare("INSERT INTO schema_migrations(version) VALUES (?)");
    for (const pythonMigration of Object.values(TYPESCRIPT_TO_PYTHON_MIGRATION_BASELINES).flat()) {
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
});
