import Database from "better-sqlite3";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

import { CampaignStore } from "../src/mission/campaign-store.js";
import { MissionStore } from "../src/mission/store.js";
import { RuntimeSessionEventStore } from "../src/session/runtime-events.js";
import { SessionStore } from "../src/session/store.js";
import { enableWal } from "../src/storage/sqlite-wal.js";

const TS_ROOT = join(import.meta.dirname, "..");

async function holdWriteLockBriefly(dbPath: string): Promise<{ exited: Promise<unknown> }> {
  // better-sqlite3 is synchronous, so the writer must live in another process
  // to release its lock while the store under test is retrying.
  const writer = spawn(
    process.execPath,
    [
      "-e",
      [
        "const Database = require('better-sqlite3');",
        "const db = new Database(process.argv[1]);",
        "db.exec('BEGIN IMMEDIATE; CREATE TABLE held_by_writer (value TEXT)');",
        "process.stdout.write('locked\\n');",
        "setTimeout(() => { db.exec('COMMIT'); db.close(); }, 200);",
      ].join("\n"),
      dbPath,
    ],
    { cwd: TS_ROOT },
  );
  const exited = new Promise((resolve) => writer.on("close", resolve));
  await new Promise<void>((resolve, reject) => {
    writer.stdout.once("data", () => resolve());
    writer.once("error", reject);
  });
  return { exited };
}

function journalMode(dbPath: string): unknown {
  const db = new Database(dbPath, { readonly: true });
  try {
    return db.pragma("journal_mode", { simple: true });
  } finally {
    db.close();
  }
}

const STORES: Array<[string, (dbPath: string) => { close(): void }]> = [
  ["mission store", (dbPath) => new MissionStore(dbPath)],
  ["campaign store", (dbPath) => new CampaignStore(dbPath)],
  ["session store", (dbPath) => new SessionStore(dbPath)],
  ["runtime session event store", (dbPath) => new RuntimeSessionEventStore(dbPath)],
];

describe("sqlite WAL enablement", () => {
  let dir: string;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "ac-sqlite-wal-"));
  });

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  it.each(STORES)(
    "%s waits for a writer before enabling WAL on a fresh database",
    async (_name, openStore) => {
      const dbPath = join(dir, "fresh.db");
      const writer = await holdWriteLockBriefly(dbPath);

      try {
        openStore(dbPath).close();
      } finally {
        await writer.exited;
      }

      expect(journalMode(dbPath)).toBe("wal");
    },
  );

  it("accepts an in-memory database, which reports memory mode", () => {
    const db = new Database(":memory:");
    try {
      enableWal(db);

      expect(db.pragma("journal_mode", { simple: true })).toBe("memory");
    } finally {
      db.close();
    }
  });
});
