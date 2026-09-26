import Database from "better-sqlite3";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { spawn } from "node:child_process";
import { mkdtempSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

import { asDbPath } from "../src/domain/ids.js";
import { SQLiteStore } from "../src/storage/sqlite-store.js";

const TS_ROOT = join(import.meta.dirname, "..");
const MIGRATIONS_DIR = join(TS_ROOT, "migrations");
const SQLITE_STORE_MODULE = join(TS_ROOT, "src", "storage", "sqlite-store.ts");
const TSX = join(TS_ROOT, "node_modules", ".bin", "tsx");
const CONCURRENT_MIGRATORS = 4;
const FRESH_DATABASES = 3;
const START_DELAY_MS = 2_000;

function runMigrator(
  scriptPath: string,
  dbPath: string,
  startAt: number,
): Promise<{ code: number | null; stderr: string }> {
  return new Promise((resolve, reject) => {
    const child = spawn(TSX, [scriptPath, dbPath, MIGRATIONS_DIR, String(startAt)], {
      env: { ...process.env, NODE_NO_WARNINGS: "1" },
    });
    let stderr = "";
    child.stderr.on("data", (chunk: Buffer) => {
      stderr += chunk.toString();
    });
    child.on("error", reject);
    child.on("close", (code) => resolve({ code, stderr }));
  });
}

describe("storage migration concurrency", () => {
  let dir: string;
  let scriptPath: string;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "ac-storage-migration-concurrency-"));
    scriptPath = join(dir, "migrate.mts");
    writeFileSync(
      scriptPath,
      [
        `import { SQLiteStore } from ${JSON.stringify(SQLITE_STORE_MODULE)};`,
        "const [dbPath, migrationsDir, startAt] = process.argv.slice(2);",
        "while (Date.now() < Number(startAt)) {}",
        "new SQLiteStore(dbPath).migrate(migrationsDir);",
      ].join("\n"),
    );
  });

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  it("waits for a writer before enabling WAL on a fresh database", async () => {
    const dbPath = join(dir, "fresh.db");
    const writer = spawn(
      process.execPath,
      [
        "-e",
        [
          "const Database = require('better-sqlite3');",
          "const db = new Database(process.argv[1]);",
          "db.exec('BEGIN IMMEDIATE; CREATE TABLE held_by_writer (value TEXT)');",
          "process.stdout.write('locked\\n');",
          "setTimeout(() => { db.exec('COMMIT'); db.close(); }, 300);",
        ].join("\n"),
        dbPath,
      ],
      { cwd: TS_ROOT },
    );
    const writerExited = new Promise((resolve) => writer.on("close", resolve));
    await new Promise<void>((resolve, reject) => {
      writer.stdout.once("data", () => resolve());
      writer.once("error", reject);
    });

    try {
      const store = new SQLiteStore(asDbPath(dbPath));
      store.close();
    } finally {
      await writerExited;
    }

    const db = new Database(dbPath, { readonly: true });
    try {
      expect(db.pragma("journal_mode", { simple: true })).toBe("wal");
    } finally {
      db.close();
    }
  });

  it("applies each migration once when processes migrate a fresh database together", async () => {
    const expected = readdirSync(MIGRATIONS_DIR)
      .filter((file) => file.endsWith(".sql"))
      .sort();

    for (let attempt = 0; attempt < FRESH_DATABASES; attempt += 1) {
      const dbPath = join(dir, `fresh-${attempt}.db`);
      const startAt = Date.now() + START_DELAY_MS;
      const results = await Promise.all(
        Array.from({ length: CONCURRENT_MIGRATORS }, () =>
          runMigrator(scriptPath, dbPath, startAt),
        ),
      );

      expect(results.filter((result) => result.code !== 0).map((result) => result.stderr)).toEqual(
        [],
      );
      const db = new Database(dbPath, { readonly: true });
      try {
        const applied = (
          db.prepare("SELECT filename FROM schema_version ORDER BY filename").all() as Array<{
            filename: string;
          }>
        ).map((row) => row.filename);
        expect(applied).toEqual(expected);
      } finally {
        db.close();
      }
    }
  }, 60_000);
});
