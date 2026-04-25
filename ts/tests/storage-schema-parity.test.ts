import Database from "better-sqlite3";
import { mkdtempSync, readFileSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  SCHEMA_PARITY_LEDGER_TABLES,
  SCHEMA_PARITY_PYTHON_ONLY_TABLES,
  SCHEMA_PARITY_SHARED_TABLES,
  SCHEMA_PARITY_TYPESCRIPT_ONLY_TABLES,
} from "../src/storage/schema-parity-manifest.js";
import { migrateDatabase } from "../src/storage/storage-migration-workflow.js";

const TYPESCRIPT_MIGRATIONS_DIR = join(import.meta.dirname, "..", "migrations");
const PYTHON_MIGRATIONS_DIR = join(
  import.meta.dirname,
  "..",
  "..",
  "autocontext",
  "migrations",
);

type ColumnSnapshot = {
  defaultValue: string | null;
  name: string;
  notNull: boolean;
  primaryKey: number;
  type: string;
};

type ForeignKeySnapshot = {
  from: string;
  match: string;
  onDelete: string;
  onUpdate: string;
  table: string;
  to: string | null;
};

type IndexSnapshot = {
  columns: Array<{
    collation: string | null;
    descending: boolean;
    name: string | null;
  }>;
  name: string;
  unique: boolean;
};

type TableSnapshot = {
  columns: ColumnSnapshot[];
  foreignKeys: ForeignKeySnapshot[];
  indexes: IndexSnapshot[];
};

function quoteIdentifier(identifier: string): string {
  return `"${identifier.replaceAll("\"", "\"\"")}"`;
}

function applyPythonMigrations(db: Database.Database): void {
  for (const migration of readdirSync(PYTHON_MIGRATIONS_DIR).filter((file) => file.endsWith(".sql")).sort()) {
    db.exec(readFileSync(join(PYTHON_MIGRATIONS_DIR, migration), "utf8"));
  }
}

function listDomainTables(db: Database.Database): string[] {
  return (
    db
      .prepare(
        `SELECT name
           FROM sqlite_schema
          WHERE type = 'table'
            AND name NOT LIKE 'sqlite_%'
          ORDER BY name`,
      )
      .all() as Array<{ name: string }>
  )
    .map((row) => row.name)
    .filter((name) => !SCHEMA_PARITY_LEDGER_TABLES.includes(name as typeof SCHEMA_PARITY_LEDGER_TABLES[number]));
}

function snapshotTable(db: Database.Database, tableName: string): TableSnapshot {
  const tableIdentifier = quoteIdentifier(tableName);
  const columns = (
    db.prepare(`PRAGMA table_info(${tableIdentifier})`).all() as Array<{
      dflt_value: string | null;
      name: string;
      notnull: number;
      pk: number;
      type: string;
    }>
  )
    .map((column) => ({
      defaultValue: column.dflt_value,
      name: column.name,
      notNull: column.notnull === 1,
      primaryKey: column.pk,
      type: column.type,
    }))
    .sort((left, right) => left.name.localeCompare(right.name));

  const indexes = (
    db.prepare(`PRAGMA index_list(${tableIdentifier})`).all() as Array<{
      name: string;
      origin: string;
      unique: number;
    }>
  )
    .filter((index) => index.origin === "c")
    .map((index) => {
      const columnsForIndex = (
        db.prepare(`PRAGMA index_xinfo(${quoteIdentifier(index.name)})`).all() as Array<{
          coll: string | null;
          desc: number;
          key: number;
          name: string | null;
          seqno: number;
        }>
      )
        .filter((column) => column.key === 1)
        .sort((left, right) => left.seqno - right.seqno)
        .map((column) => ({
          collation: column.coll,
          descending: column.desc === 1,
          name: column.name,
        }));
      return {
        columns: columnsForIndex,
        name: index.name,
        unique: index.unique === 1,
      };
    })
    .sort((left, right) => left.name.localeCompare(right.name));

  const foreignKeys = (
    db.prepare(`PRAGMA foreign_key_list(${tableIdentifier})`).all() as Array<{
      from: string;
      match: string;
      on_delete: string;
      on_update: string;
      table: string;
      to: string | null;
    }>
  )
    .map((foreignKey) => ({
      from: foreignKey.from,
      match: foreignKey.match,
      onDelete: foreignKey.on_delete,
      onUpdate: foreignKey.on_update,
      table: foreignKey.table,
      to: foreignKey.to,
    }))
    .sort((left, right) => `${left.table}.${left.from}`.localeCompare(`${right.table}.${right.from}`));

  return { columns, foreignKeys, indexes };
}

describe("storage schema parity", () => {
  let dir: string;
  let typescriptDb: Database.Database;
  let pythonDb: Database.Database;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "ac-schema-parity-"));
    typescriptDb = new Database(join(dir, "typescript.db"));
    pythonDb = new Database(join(dir, "python.db"));
    migrateDatabase(typescriptDb, TYPESCRIPT_MIGRATIONS_DIR);
    applyPythonMigrations(pythonDb);
  });

  afterEach(() => {
    typescriptDb.close();
    pythonDb.close();
    rmSync(dir, { recursive: true, force: true });
  });

  it("keeps every shared storage table structurally aligned", () => {
    for (const tableName of SCHEMA_PARITY_SHARED_TABLES) {
      expect(snapshotTable(typescriptDb, tableName), tableName).toEqual(snapshotTable(pythonDb, tableName));
    }
  });

  it("documents intentionally one-sided storage tables", () => {
    const typescriptTables = new Set(listDomainTables(typescriptDb));
    const pythonTables = new Set(listDomainTables(pythonDb));

    const pythonOnly = [...pythonTables].filter((table) => !typescriptTables.has(table)).sort();
    const typescriptOnly = [...typescriptTables].filter((table) => !pythonTables.has(table)).sort();

    expect(pythonOnly).toEqual(
      SCHEMA_PARITY_PYTHON_ONLY_TABLES.map((entry) => entry.table).sort(),
    );
    expect(typescriptOnly).toEqual(
      SCHEMA_PARITY_TYPESCRIPT_ONLY_TABLES.map((entry) => entry.table).sort(),
    );
  });

  it("keeps the parity manifest internally consistent", () => {
    const shared = new Set<string>(SCHEMA_PARITY_SHARED_TABLES);
    expect(shared.size).toBe(SCHEMA_PARITY_SHARED_TABLES.length);

    const pythonOnly = new Set(SCHEMA_PARITY_PYTHON_ONLY_TABLES.map((entry) => entry.table));
    const typescriptOnly = new Set(SCHEMA_PARITY_TYPESCRIPT_ONLY_TABLES.map((entry) => entry.table));

    for (const tableName of shared) {
      expect(pythonOnly.has(tableName), tableName).toBe(false);
      expect(typescriptOnly.has(tableName), tableName).toBe(false);
    }
    for (const tableName of pythonOnly) {
      expect(typescriptOnly.has(tableName), tableName).toBe(false);
    }
  });
});
