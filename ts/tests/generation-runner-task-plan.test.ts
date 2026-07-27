import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { LoopController } from "../src/loop/controller.js";
import { EventStreamEmitter } from "../src/loop/events.js";
import { GenerationRunner } from "../src/loop/generation-runner.js";
import { HookBus, HookEvents, HookResult } from "../src/extensions/index.js";
import { DeterministicProvider } from "../src/providers/deterministic.js";
import { GridCtfScenario } from "../src/scenarios/grid-ctf.js";
import { SQLiteStore } from "../src/storage/index.js";

const TEST_DIRECTORY = dirname(fileURLToPath(import.meta.url));

describe("GenerationRunner task plans", () => {
  let root: string;

  beforeEach(() => {
    root = mkdtempSync(join(tmpdir(), "autoctx-task-plan-runner-"));
  });

  afterEach(() => {
    rmSync(root, { recursive: true, force: true });
  });

  it("publishes an ordered replan on rollback before completing the run", async () => {
    const store = createStore(root, "rollback.db");
    const events = new EventStreamEmitter(join(root, "rollback-events.ndjson"));
    const emitted: Array<{ event: string; payload: Record<string, unknown> }> = [];
    events.subscribe((event, payload) => {
      emitted.push({ event, payload });
    });
    const runner = new GenerationRunner({
      provider: new DeterministicProvider(),
      scenario: new GridCtfScenario(),
      store,
      runsRoot: join(root, "runs"),
      knowledgeRoot: join(root, "knowledge"),
      matchesPerGeneration: 1,
      maxRetries: 0,
      minDelta: 2,
      events,
    });

    try {
      await runner.run("built_in_plan", 1);
    } finally {
      store.close();
    }

    const planEvents = emitted.filter((entry) => entry.event === "task_plan_updated");
    expect(emitted.at(0)?.event).toBe("run_started");
    expect(planEvents.at(0)?.payload).toMatchObject({
      update_kind: "initial",
      plan_revision: 1,
      active_step_id: "prepare_run",
    });
    expect(planEvents.find((entry) => entry.payload.update_kind === "replan")?.payload)
      .toMatchObject({
        plan_revision: 2,
        active_step_id: "iterate_strategies",
        summary: "Adjusting the strategy approach after a recovery signal.",
      });
    expect(planEvents.at(-1)?.payload).toMatchObject({
      plan_revision: 2,
      active_step_id: null,
    });
    expect(planEvents.at(-1)?.payload.steps).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: "finalize_run", status: "completed" }),
      ]),
    );
    expect(emitted.findLastIndex((entry) => entry.event === "task_plan_updated"))
      .toBeLessThan(emitted.findIndex((entry) => entry.event === "run_completed"));
  });

  it("publishes an interrupted terminal plan before propagating a stop", async () => {
    const store = createStore(root, "stopped.db");
    const events = new EventStreamEmitter(join(root, "stopped-events.ndjson"));
    const emitted: Array<{ event: string; payload: Record<string, unknown> }> = [];
    events.subscribe((event, payload) => {
      emitted.push({ event, payload });
    });
    const controller = new LoopController();
    controller.requestStop("built_in_stop", "stop-command");
    const hookBus = new HookBus();
    hookBus.on(
      HookEvents.RUN_END,
      () => new HookResult({
        block: true,
        reason: "terminal hook cannot replace the resolved stop",
      }),
    );
    const runner = new GenerationRunner({
      provider: new DeterministicProvider(),
      scenario: new GridCtfScenario(),
      store,
      runsRoot: join(root, "runs"),
      knowledgeRoot: join(root, "knowledge"),
      controller,
      events,
      hookBus,
    });

    try {
      await expect(runner.run("built_in_stop", 1)).rejects.toMatchObject({
        name: "RunStopRequestedError",
        runId: "built_in_stop",
        commandId: "stop-command",
      });
    } finally {
      store.close();
    }

    const terminalPlan = emitted
      .filter((entry) => entry.event === "task_plan_updated")
      .at(-1)?.payload;
    expect(terminalPlan?.active_step_id).toBeNull();
    expect(terminalPlan?.steps).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: "prepare_run", status: "interrupted" }),
      ]),
    );
    expect(emitted.some((entry) => entry.event === "run_completed")).toBe(false);
    expect(emitted.some((entry) => entry.event === "run_failed")).toBe(false);
  });

  it("turns a blocked completion hook into one failed terminal outcome", async () => {
    const store = createStore(root, "blocked-completion.db");
    const events = new EventStreamEmitter(join(root, "blocked-completion-events.ndjson"));
    const emitted: Array<{ event: string; payload: Record<string, unknown> }> = [];
    events.subscribe((event, payload) => {
      emitted.push({ event, payload });
    });
    const hookBus = new HookBus();
    hookBus.on(HookEvents.RUN_END, (event) =>
      event.payload.status === "completed"
        ? new HookResult({ block: true, reason: "completion policy rejected" })
        : undefined,
    );
    const runner = new GenerationRunner({
      provider: new DeterministicProvider(),
      scenario: new GridCtfScenario(),
      store,
      runsRoot: join(root, "runs"),
      knowledgeRoot: join(root, "knowledge"),
      matchesPerGeneration: 1,
      maxRetries: 0,
      minDelta: 0,
      events,
      hookBus,
    });

    try {
      await expect(runner.run("blocked_completion", 1)).rejects.toThrow(
        "extension hook blocked run_end: completion policy rejected",
      );
    } finally {
      store.close();
    }

    const terminalPlan = emitted
      .filter((entry) => entry.event === "task_plan_updated")
      .at(-1)?.payload;
    expect(terminalPlan?.active_step_id).toBeNull();
    expect(terminalPlan?.steps).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: "finalize_run", status: "failed" }),
      ]),
    );
    expect(emitted.some((entry) => entry.event === "run_completed")).toBe(false);
    expect(emitted.filter((entry) => entry.event === "run_failed")).toHaveLength(1);
    expect(emitted.findLastIndex((entry) => entry.event === "task_plan_updated"))
      .toBeLessThan(emitted.findIndex((entry) => entry.event === "run_failed"));
  });
});

function createStore(root: string, name: string): SQLiteStore {
  const store = new SQLiteStore(join(root, name));
  store.migrate(join(TEST_DIRECTORY, "..", "migrations"));
  return store;
}
