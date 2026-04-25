import { describe, it, expect, beforeEach } from "vitest";
import { mkdtempSync, cpSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { SQLiteStore } from "../src/storage/index.js";

const MIGRATIONS_DIR = join(import.meta.dirname, "..", "migrations");

function createStore(): SQLiteStore {
  const dir = mkdtempSync(join(tmpdir(), "autocontext-test-"));
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

  it("persists research hub metadata records", () => {
    store.upsertNotebook({
      sessionId: "session-1",
      scenarioName: "grid_ctf",
      currentObjective: "Hold center.",
    });
    store.upsertHubSession("session-1", {
      owner: "operator",
      status: "active",
      shared: true,
      metadata: { source: "test" },
    });

    expect(store.getHubSession("session-1")).toMatchObject({
      session_id: "session-1",
      owner: "operator",
      shared: true,
      metadata: { source: "test" },
    });

    store.saveHubPackageRecord({
      packageId: "pkg-1",
      scenarioName: "grid_ctf",
      scenarioFamily: "game",
      sourceRunId: "run-1",
      sourceGeneration: 1,
      title: "Grid package",
      description: "A package.",
      promotionLevel: "experimental",
      bestScore: 0.7,
      bestElo: 1050,
      payloadPath: "_hub/packages/pkg-1/shared_package.json",
      strategyPackagePath: "_hub/packages/pkg-1/strategy_package.json",
      tags: ["grid_ctf"],
      metadata: { source_session_id: "session-1" },
      createdAt: "2026-04-25T00:00:00.000Z",
    });
    expect(store.getHubPackageRecord("pkg-1")).toMatchObject({
      package_id: "pkg-1",
      scenario_name: "grid_ctf",
      tags: ["grid_ctf"],
      metadata: { source_session_id: "session-1" },
    });

    store.saveHubResultRecord({
      resultId: "res-1",
      scenarioName: "grid_ctf",
      runId: "run-1",
      packageId: "pkg-1",
      title: "Grid result",
      bestScore: 0.7,
      bestElo: 1050,
      payloadPath: "_hub/results/res-1.json",
      tags: ["grid_ctf"],
      metadata: { scenario_family: "game" },
      createdAt: "2026-04-25T00:00:00.000Z",
    });
    expect(store.getHubResultRecord("res-1")).toMatchObject({
      result_id: "res-1",
      package_id: "pkg-1",
      tags: ["grid_ctf"],
    });

    store.saveHubPromotionRecord({
      eventId: "promo-1",
      packageId: "pkg-1",
      sourceRunId: "run-1",
      action: "promote",
      actor: "operator",
      label: "experimental",
      metadata: { source_generation: 1 },
      createdAt: "2026-04-25T00:00:00.000Z",
    });
    expect(store.listHubPromotionRecords()).toContainEqual(expect.objectContaining({
      event_id: "promo-1",
      metadata: { source_generation: 1 },
    }));
  });
});
