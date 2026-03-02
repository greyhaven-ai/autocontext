import { describe, it, expect, beforeEach } from "vitest";
import { mkdtempSync, cpSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { SQLiteStore } from "../src/storage/index.js";

const MIGRATIONS_DIR = join(import.meta.dirname, "..", "migrations");

function createStore(): SQLiteStore {
  const dir = mkdtempSync(join(tmpdir(), "mts-test-"));
  const store = new SQLiteStore(join(dir, "test.db"));
  store.migrate(MIGRATIONS_DIR);
  return store;
}

describe("SQLiteStore", () => {
  let store: SQLiteStore;

  beforeEach(() => {
    store = createStore();
  });

  it("enqueue and dequeue", () => {
    store.enqueueTask("t1", "spec_a");
    const task = store.dequeueTask();
    expect(task).not.toBeNull();
    expect(task!.id).toBe("t1");
    expect(task!.status).toBe("running");
  });

  it("empty queue returns null", () => {
    expect(store.dequeueTask()).toBeNull();
  });

  it("priority ordering", () => {
    store.enqueueTask("low", "s", 1);
    store.enqueueTask("high", "s", 10);
    store.enqueueTask("med", "s", 5);

    expect(store.dequeueTask()!.id).toBe("high");
    expect(store.dequeueTask()!.id).toBe("med");
    expect(store.dequeueTask()!.id).toBe("low");
  });

  it("FIFO within same priority", () => {
    store.enqueueTask("first", "s", 5);
    store.enqueueTask("second", "s", 5);
    store.enqueueTask("third", "s", 5);

    expect(store.dequeueTask()!.id).toBe("first");
    expect(store.dequeueTask()!.id).toBe("second");
    expect(store.dequeueTask()!.id).toBe("third");
  });

  it("running tasks not re-dequeued", () => {
    store.enqueueTask("t1", "s");
    store.dequeueTask();
    expect(store.dequeueTask()).toBeNull();
  });

  it("complete task", () => {
    store.enqueueTask("t1", "s");
    store.dequeueTask();
    store.completeTask("t1", 0.9, "output", 2, true);
    const task = store.getTask("t1");
    expect(task!.status).toBe("completed");
    expect(task!.best_score).toBe(0.9);
    expect(task!.met_threshold).toBe(1);
  });

  it("fail task", () => {
    store.enqueueTask("t1", "s");
    store.dequeueTask();
    store.failTask("t1", "boom");
    const task = store.getTask("t1");
    expect(task!.status).toBe("failed");
    expect(task!.error).toBe("boom");
  });

  it("pending count", () => {
    store.enqueueTask("t1", "s");
    store.enqueueTask("t2", "s");
    expect(store.pendingTaskCount()).toBe(2);
    store.dequeueTask();
    expect(store.pendingTaskCount()).toBe(1);
  });

  it("scheduled task not dequeued early", () => {
    store.enqueueTask("future", "s", 10, undefined, "2099-01-01T00:00:00");
    store.enqueueTask("now", "s", 1);
    expect(store.dequeueTask()!.id).toBe("now");
    expect(store.dequeueTask()).toBeNull();
  });

  it("migrate is idempotent with version tracking", () => {
    // Running migrate again should not throw (migrations already applied)
    store.migrate(MIGRATIONS_DIR);
    // Store still works
    store.enqueueTask("t1", "s");
    expect(store.dequeueTask()!.id).toBe("t1");
  });
});
