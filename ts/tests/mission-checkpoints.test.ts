/**
 * Tests for AC-411: Mission checkpointing, subgoals, and durable state.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-checkpoint-"));
}

// ---------------------------------------------------------------------------
// MissionSpec — declarative mission definition
// ---------------------------------------------------------------------------

describe("MissionSpec", () => {
  it("MissionSpecSchema validates a complete spec", async () => {
    const { MissionSpecSchema } = await import("../src/mission/types.js");
    const spec = MissionSpecSchema.parse({
      name: "Ship login feature",
      goal: "Implement OAuth login endpoint with tests",
      verifierType: "test_suite",
      budget: { maxSteps: 50, maxCostUsd: 10.0 },
      subgoals: [
        { description: "Create migration", priority: 1 },
        { description: "Implement handler", priority: 2 },
        { description: "Write tests", priority: 3 },
      ],
    });
    expect(spec.name).toBe("Ship login feature");
    expect(spec.subgoals!.length).toBe(3);
  });

  it("MissionSpecSchema works with minimal fields", async () => {
    const { MissionSpecSchema } = await import("../src/mission/types.js");
    const spec = MissionSpecSchema.parse({
      name: "Quick task",
      goal: "Do the thing",
    });
    expect(spec.verifierType).toBeUndefined();
    expect(spec.subgoals).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// Subgoals
// ---------------------------------------------------------------------------

describe("Subgoals", () => {
  let dir: string;
  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("store creates and retrieves subgoals", async () => {
    const { MissionStore } = await import("../src/mission/store.js");
    const store = new MissionStore(join(dir, "test.db"));
    const mId = store.createMission({ name: "Test", goal: "g" });

    const sgId = store.addSubgoal(mId, { description: "Write tests", priority: 1 });
    expect(sgId).toBeDefined();

    const subgoals = store.getSubgoals(mId);
    expect(subgoals.length).toBe(1);
    expect(subgoals[0].description).toBe("Write tests");
    expect(subgoals[0].priority).toBe(1);
    expect(subgoals[0].status).toBe("pending");
    store.close();
  });

  it("store updates subgoal status", async () => {
    const { MissionStore } = await import("../src/mission/store.js");
    const store = new MissionStore(join(dir, "test.db"));
    const mId = store.createMission({ name: "Test", goal: "g" });
    const sgId = store.addSubgoal(mId, { description: "Write tests", priority: 1 });

    store.updateSubgoalStatus(sgId, "completed");
    const subgoals = store.getSubgoals(mId);
    expect(subgoals[0].status).toBe("completed");
    store.close();
  });

  it("rejects subgoal statuses outside the exported contract", async () => {
    const { MissionStore } = await import("../src/mission/store.js");
    const store = new MissionStore(join(dir, "test.db"));
    const mId = store.createMission({ name: "Test", goal: "g" });
    const sgId = store.addSubgoal(mId, { description: "Write tests", priority: 1 });

    expect(() => {
      (store.updateSubgoalStatus as (id: string, status: string) => void)(sgId, "banana");
    }).toThrow();

    const subgoals = store.getSubgoals(mId);
    expect(subgoals[0].status).toBe("pending");
    store.close();
  });

  it("reopening a terminal subgoal clears completedAt", async () => {
    const { MissionStore } = await import("../src/mission/store.js");
    const store = new MissionStore(join(dir, "test.db"));
    const mId = store.createMission({ name: "Test", goal: "g" });
    const sgId = store.addSubgoal(mId, { description: "Write tests", priority: 1 });

    store.updateSubgoalStatus(sgId, "completed");
    expect(store.getSubgoals(mId)[0].completedAt).toBeDefined();

    store.updateSubgoalStatus(sgId, "active");
    const subgoal = store.getSubgoals(mId)[0];
    expect(subgoal.status).toBe("active");
    expect(subgoal.completedAt).toBeUndefined();
    store.close();
  });

  it("subgoals are ordered by priority", async () => {
    const { MissionStore } = await import("../src/mission/store.js");
    const store = new MissionStore(join(dir, "test.db"));
    const mId = store.createMission({ name: "Test", goal: "g" });

    store.addSubgoal(mId, { description: "Low prio", priority: 3 });
    store.addSubgoal(mId, { description: "High prio", priority: 1 });
    store.addSubgoal(mId, { description: "Mid prio", priority: 2 });

    const subgoals = store.getSubgoals(mId);
    expect(subgoals[0].description).toBe("High prio");
    expect(subgoals[1].description).toBe("Mid prio");
    expect(subgoals[2].description).toBe("Low prio");
    store.close();
  });
});

// ---------------------------------------------------------------------------
// Checkpointing — save/restore full mission state
// ---------------------------------------------------------------------------

describe("Mission checkpoints", () => {
  let dir: string;
  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("saveCheckpoint writes a JSON snapshot to disk", async () => {
    const { MissionStore } = await import("../src/mission/store.js");
    const { saveCheckpoint } = await import("../src/mission/checkpoint.js");
    const store = new MissionStore(join(dir, "test.db"));
    const mId = store.createMission({ name: "Test", goal: "g" });
    store.addStep(mId, { description: "Step 1" });
    store.addSubgoal(mId, { description: "Subgoal A", priority: 1 });

    const checkpointDir = join(dir, "checkpoints");
    const path = saveCheckpoint(store, mId, checkpointDir);
    expect(existsSync(path)).toBe(true);

    const data = JSON.parse(readFileSync(path, "utf-8"));
    expect(data.mission.id).toBe(mId);
    expect(data.steps.length).toBe(1);
    expect(data.subgoals.length).toBe(1);
    store.close();
  });

  it("loadCheckpoint restores mission state into a new store", async () => {
    const { MissionStore } = await import("../src/mission/store.js");
    const { saveCheckpoint, loadCheckpoint } = await import("../src/mission/checkpoint.js");

    // Create and checkpoint
    const store1 = new MissionStore(join(dir, "source.db"));
    const mId = store1.createMission({ name: "Durable", goal: "Survive restart" });
    store1.addStep(mId, { description: "Did something" });
    store1.addSubgoal(mId, { description: "Goal A", priority: 1 });
    store1.recordVerification(mId, { passed: false, reason: "Not yet" });

    const checkpointDir = join(dir, "checkpoints");
    const path = saveCheckpoint(store1, mId, checkpointDir);
    store1.close();

    // Restore into a fresh store
    const store2 = new MissionStore(join(dir, "target.db"));
    const restoredId = loadCheckpoint(store2, path);

    expect(restoredId).toBe(mId);
    const mission = store2.getMission(restoredId);
    expect(mission!.name).toBe("Durable");
    expect(store2.getSteps(restoredId).length).toBe(1);
    expect(store2.getSubgoals(restoredId).length).toBe(1);
    expect(store2.getVerifications(restoredId).length).toBe(1);
    store2.close();
  });

  it("loadCheckpoint restores multiple verification rows without ID collisions", async () => {
    const { MissionStore } = await import("../src/mission/store.js");
    const { saveCheckpoint, loadCheckpoint } = await import("../src/mission/checkpoint.js");

    const store1 = new MissionStore(join(dir, "source.db"));
    const mId = store1.createMission({ name: "Durable", goal: "Survive restart" });
    store1.recordVerification(mId, {
      passed: false,
      reason: "First verifier pass",
      metadata: { attempt: 1 },
    });
    store1.recordVerification(mId, {
      passed: false,
      reason: "Second verifier pass",
      metadata: { attempt: 2 },
    });

    const checkpointDir = join(dir, "checkpoints");
    const path = saveCheckpoint(store1, mId, checkpointDir);
    store1.close();

    const store2 = new MissionStore(join(dir, "target.db"));
    const restoredId = loadCheckpoint(store2, path);
    const verifications = store2.getVerifications(restoredId);

    expect(verifications).toHaveLength(2);
    expect(verifications.map((v) => v.reason)).toEqual([
      "First verifier pass",
      "Second verifier pass",
    ]);
    expect(verifications[1].metadata).toEqual({ attempt: 2 });
    store2.close();
  });

  it("checkpoint includes budget usage", async () => {
    const { MissionStore } = await import("../src/mission/store.js");
    const { saveCheckpoint } = await import("../src/mission/checkpoint.js");
    const store = new MissionStore(join(dir, "test.db"));
    const mId = store.createMission({
      name: "Budgeted",
      goal: "g",
      budget: { maxSteps: 10, maxCostUsd: 5.0 },
    });
    store.addStep(mId, { description: "s1" });
    store.addStep(mId, { description: "s2" });

    const checkpointDir = join(dir, "checkpoints");
    const path = saveCheckpoint(store, mId, checkpointDir);
    const data = JSON.parse(readFileSync(path, "utf-8"));

    expect(data.budgetUsage.stepsUsed).toBe(2);
    expect(data.budgetUsage.maxSteps).toBe(10);
    store.close();
  });
});

// ---------------------------------------------------------------------------
// Budget usage tracking
// ---------------------------------------------------------------------------

describe("Budget usage", () => {
  let dir: string;
  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("getBudgetUsage returns steps used vs budget", async () => {
    const { MissionStore } = await import("../src/mission/store.js");
    const store = new MissionStore(join(dir, "test.db"));
    const mId = store.createMission({
      name: "Test",
      goal: "g",
      budget: { maxSteps: 5 },
    });
    store.addStep(mId, { description: "s1" });
    store.addStep(mId, { description: "s2" });

    const usage = store.getBudgetUsage(mId);
    expect(usage.stepsUsed).toBe(2);
    expect(usage.maxSteps).toBe(5);
    expect(usage.exhausted).toBe(false);
    store.close();
  });

  it("budget is exhausted when steps exceed max", async () => {
    const { MissionStore } = await import("../src/mission/store.js");
    const store = new MissionStore(join(dir, "test.db"));
    const mId = store.createMission({
      name: "Test",
      goal: "g",
      budget: { maxSteps: 2 },
    });
    store.addStep(mId, { description: "s1" });
    store.addStep(mId, { description: "s2" });

    const usage = store.getBudgetUsage(mId);
    expect(usage.exhausted).toBe(true);
    store.close();
  });

  it("no budget means never exhausted", async () => {
    const { MissionStore } = await import("../src/mission/store.js");
    const store = new MissionStore(join(dir, "test.db"));
    const mId = store.createMission({ name: "Test", goal: "g" });
    store.addStep(mId, { description: "s1" });

    const usage = store.getBudgetUsage(mId);
    expect(usage.exhausted).toBe(false);
    expect(usage.maxSteps).toBeUndefined();
    store.close();
  });
});
