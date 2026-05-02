import { describe, expect, it } from "vitest";

import { enqueueTask } from "../src/execution/task-runner.js";
import type { TaskQueueEnqueueStore } from "../src/execution/task-queue-store.js";
import type { TaskQueueRow } from "../src/storage/index.js";

function makeTask(id: string, specName: string): TaskQueueRow {
  return {
    id,
    spec_name: specName,
    status: "pending",
    priority: 0,
    config_json: null,
    scheduled_at: null,
    started_at: null,
    completed_at: null,
    best_score: null,
    best_output: null,
    total_rounds: null,
    met_threshold: 0,
    result_json: null,
    error: null,
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-01T00:00:00Z",
  };
}

describe("task queue store contract", () => {
  it("lets hosted stores provide the queue surface without subclassing SQLiteStore", () => {
    const rows = new Map<string, TaskQueueRow>();
    const store = {
      enqueueTask: (id: string, specName: string) => {
        rows.set(id, makeTask(id, specName));
      },
      dequeueTask: () => rows.values().next().value ?? null,
      getTask: (taskId: string) => rows.get(taskId) ?? null,
      completeTask: (taskId: string) => {
        const task = rows.get(taskId);
        if (task) rows.set(taskId, { ...task, status: "completed" });
      },
      failTask: (taskId: string, error: string) => {
        const task = rows.get(taskId);
        if (task) rows.set(taskId, { ...task, status: "failed", error });
      },
    } satisfies TaskQueueEnqueueStore;

    const taskId = enqueueTask(store, "hosted-spec", {
      taskPrompt: "Do the thing",
      priority: 5,
    });

    expect(store.getTask(taskId)?.spec_name).toBe("hosted-spec");
  });

  it("lets hosted Postgres-style stores expose async queue methods", async () => {
    const rows = new Map<string, TaskQueueRow>();
    const store = {
      enqueueTask: async (id: string, specName: string) => {
        rows.set(id, makeTask(id, specName));
      },
      dequeueTask: async () => rows.values().next().value ?? null,
      getTask: async (taskId: string) => rows.get(taskId) ?? null,
      completeTask: async (taskId: string) => {
        const task = rows.get(taskId);
        if (task) rows.set(taskId, { ...task, status: "completed" });
      },
      failTask: async (taskId: string, error: string) => {
        const task = rows.get(taskId);
        if (task) rows.set(taskId, { ...task, status: "failed", error });
      },
    } satisfies TaskQueueEnqueueStore;

    await store.enqueueTask("hosted-async", "postgres-spec");
    const task = await store.dequeueTask();
    expect(task?.spec_name).toBe("postgres-spec");

    await store.completeTask("hosted-async", 0.9, "done", 1, true);
    await expect(store.getTask("hosted-async")).resolves.toMatchObject({
      status: "completed",
    });
  });
});
