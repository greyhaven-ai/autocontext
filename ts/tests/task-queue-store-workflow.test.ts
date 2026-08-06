import Database from "better-sqlite3";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { asDbPath } from "../src/domain/ids.js";
import { SQLiteStore, type TaskQueueRow } from "../src/storage/index.js";
import {
  completeTaskRecord,
  countPendingTaskRecords,
  dequeueTaskRecord,
  enqueueTaskRecord,
  failTaskRecord,
  getTaskRecord,
  requeueStaleRunning,
} from "../src/storage/task-queue-store.js";

const MIGRATIONS_DIR = join(import.meta.dirname, "..", "migrations");

describe("task queue store workflow", () => {
  let dir: string;
  let db: Database.Database;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "ac-task-queue-store-"));
    const dbPath = join(dir, "test.db");
    const store = new SQLiteStore(asDbPath(dbPath));
    store.migrate(MIGRATIONS_DIR);
    store.close();
    db = new Database(dbPath);
  });

  afterEach(() => {
    db.close();
    rmSync(dir, { recursive: true, force: true });
  });

  it("enqueues, dequeues, counts, completes, and fetches task records", () => {
    enqueueTaskRecord(db, "task-low", "spec", 1);
    enqueueTaskRecord(db, "task-high", "spec", 10, { task_prompt: "Prompt" });

    expect(countPendingTaskRecords(db)).toBe(2);

    const dequeued = dequeueTaskRecord<TaskQueueRow>(db);
    expect(dequeued?.id).toBe("task-high");
    expect(dequeued?.status).toBe("running");

    completeTaskRecord(db, "task-high", 0.92, "Best", 3, true, '{"ok":true}');
    expect(getTaskRecord<TaskQueueRow>(db, "task-high")).toMatchObject({
      status: "completed",
      best_score: 0.92,
      met_threshold: 1,
      total_rounds: 3,
    });
    expect(countPendingTaskRecords(db)).toBe(1);
  });

  it("fails tasks and respects future scheduling when dequeuing", () => {
    enqueueTaskRecord(db, "future", "spec", 10, undefined, "2099-01-01T00:00:00");
    enqueueTaskRecord(db, "now", "spec", 1);

    expect(dequeueTaskRecord<TaskQueueRow>(db)?.id).toBe("now");
    failTaskRecord(db, "now", "boom");

    expect(getTaskRecord<TaskQueueRow>(db, "now")).toMatchObject({
      status: "failed",
      error: "boom",
    });
    expect(dequeueTaskRecord<TaskQueueRow>(db)).toBeNull();
  });
});

describe("task queue reliability (AC-906)", () => {
  let dir: string;
  let db: Database.Database;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "ac-task-queue-rel-"));
    const dbPath = join(dir, "test.db");
    const store = new SQLiteStore(asDbPath(dbPath));
    store.migrate(MIGRATIONS_DIR);
    store.close();
    db = new Database(dbPath);
  });

  afterEach(() => {
    db.close();
    rmSync(dir, { recursive: true, force: true });
  });

  it("claim increments attempts and returns the full row", () => {
    enqueueTaskRecord(db, "t1", "spec", 0);
    const claimed = dequeueTaskRecord<TaskQueueRow & { attempts: number }>(db);
    expect(claimed?.attempts).toBe(1);
    expect(claimed?.status).toBe("running");
    expect(dequeueTaskRecord(db)).toBeNull();
  });

  it("transient failure requeues below maxAttempts, dead-letters at it", () => {
    enqueueTaskRecord(db, "t1", "spec", 0);
    for (let attempt = 1; attempt <= 3; attempt++) {
      const claimed = dequeueTaskRecord(db);
      expect(claimed).not.toBeNull();
      failTaskRecord(db, "t1", `error ${attempt}`, 3, 0);
      const row = db.prepare("SELECT status, error FROM task_queue WHERE id = 't1'").get() as {
        status: string;
        error: string;
      };
      expect(row.status).toBe(attempt < 3 ? "pending" : "failed");
      expect(row.error).toBe(`error ${attempt}`);
    }
  });

  it("default fail stays terminal", () => {
    enqueueTaskRecord(db, "t1", "spec", 0);
    dequeueTaskRecord(db);
    failTaskRecord(db, "t1", "hard error");
    const row = db.prepare("SELECT status FROM task_queue WHERE id = 't1'").get() as {
      status: string;
    };
    expect(row.status).toBe("failed");
  });

  it("requeueStaleRunning recovers stranded rows and spares recent ones", () => {
    enqueueTaskRecord(db, "t1", "spec", 0);
    dequeueTaskRecord(db);
    expect(requeueStaleRunning(db, 3600)).toBe(0);
    expect(requeueStaleRunning(db, 0)).toBe(1);
    const row = db.prepare("SELECT status FROM task_queue WHERE id = 't1'").get() as {
      status: string;
    };
    expect(row.status).toBe("pending");
  });
});

describe("task queue backoff, sweep dead-letter, and store wiring (AC-906 review)", () => {
  let dir: string;
  let db: Database.Database;
  let dbPath: string;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "ac-task-queue-rev-"));
    dbPath = join(dir, "test.db");
    const store = new SQLiteStore(asDbPath(dbPath));
    store.migrate(MIGRATIONS_DIR);
    store.close();
    db = new Database(dbPath);
  });

  afterEach(() => {
    db.close();
    rmSync(dir, { recursive: true, force: true });
  });

  it("default backoff defers the next claim", () => {
    enqueueTaskRecord(db, "t1", "spec", 0);
    dequeueTaskRecord(db);
    failTaskRecord(db, "t1", "blip", 3);
    expect(dequeueTaskRecord(db)).toBeNull();
    const row = db.prepare("SELECT status, scheduled_at FROM task_queue WHERE id = 't1'").get() as {
      status: string;
      scheduled_at: string | null;
    };
    expect(row.status).toBe("pending");
    expect(row.scheduled_at).not.toBeNull();
  });

  it("sweep dead-letters a crash-looping task at the budget", () => {
    enqueueTaskRecord(db, "t1", "spec", 0);
    for (let i = 0; i < 3; i++) {
      expect(dequeueTaskRecord(db)).not.toBeNull();
      requeueStaleRunning(db, 0, 3);
    }
    const row = db.prepare("SELECT status, error FROM task_queue WHERE id = 't1'").get() as {
      status: string;
      error: string;
    };
    expect(row.status).toBe("failed");
    expect(row.error).toContain("crash-looped");
    expect(dequeueTaskRecord(db)).toBeNull();
  });

  it("SQLiteStore wires retries and stale recovery end to end", () => {
    // review-caught Critical: the facade previously dropped maxAttempts and
    // had no requeueStaleRunning, making the feature a production no-op
    const store = new SQLiteStore(asDbPath(dbPath));
    store.enqueueTask("t1", "spec");
    store.dequeueTask();
    store.failTask("t1", "transient blip", 3, 0);
    const requeued = store.getTask("t1") as { status: string } | null;
    expect(requeued?.status).toBe("pending");
    store.dequeueTask();
    expect(store.requeueStaleRunning(0, 3)).toBe(1);
    const swept = store.getTask("t1") as { status: string } | null;
    expect(swept?.status).toBe("pending");
    store.close();
  });
});
