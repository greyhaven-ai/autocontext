/**
 * Tests for AC-410: Mission primitives — data model, storage, manager, verifier.
 *
 * Foundation for verifier-driven, long-running agent goals.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { dirname } from "node:path";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-mission-"));
}

// ---------------------------------------------------------------------------
// Mission types / schemas
// ---------------------------------------------------------------------------

describe("Mission types", () => {
  it("exports MissionSchema with required fields", async () => {
    const { MissionSchema } = await import("../src/mission/types.js");
    const mission = MissionSchema.parse({
      id: "m-1",
      name: "Ship login feature",
      status: "active",
      goal: "Implement /login endpoint with OAuth",
      createdAt: new Date().toISOString(),
    });
    expect(mission.id).toBe("m-1");
    expect(mission.status).toBe("active");
  });

  it("MissionStatus enum has correct values", async () => {
    const { MissionStatusSchema } = await import("../src/mission/types.js");
    expect(MissionStatusSchema.parse("active")).toBe("active");
    expect(MissionStatusSchema.parse("paused")).toBe("paused");
    expect(MissionStatusSchema.parse("completed")).toBe("completed");
    expect(MissionStatusSchema.parse("failed")).toBe("failed");
    expect(MissionStatusSchema.parse("canceled")).toBe("canceled");
  });

  it("MissionStepSchema captures individual steps", async () => {
    const { MissionStepSchema } = await import("../src/mission/types.js");
    const step = MissionStepSchema.parse({
      id: "s-1",
      missionId: "m-1",
      description: "Create database migration",
      status: "completed",
      createdAt: new Date().toISOString(),
    });
    expect(step.missionId).toBe("m-1");
    expect(step.status).toBe("completed");
  });

  it("VerifierResultSchema captures verification outcome", async () => {
    const { VerifierResultSchema } = await import("../src/mission/types.js");
    const result = VerifierResultSchema.parse({
      passed: false,
      reason: "Tests failing: 3 errors in auth module",
      suggestions: ["Fix type error in login handler", "Add missing import"],
    });
    expect(result.passed).toBe(false);
    expect(result.suggestions.length).toBe(2);
  });
});

// ---------------------------------------------------------------------------
// MissionStore — SQLite persistence
// ---------------------------------------------------------------------------

describe("MissionStore", () => {
  let dir: string;
  let store: InstanceType<Awaited<ReturnType<typeof import("../src/mission/store.js")>>["MissionStore"]>;

  beforeEach(async () => {
    dir = makeTempDir();
    const { MissionStore } = await import("../src/mission/store.js");
    store = new MissionStore(join(dir, "test.db"));
  });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("creates a mission", () => {
    const id = store.createMission({
      name: "Ship login",
      goal: "Implement OAuth login",
    });
    expect(id).toBeDefined();
    expect(typeof id).toBe("string");
  });

  it("retrieves a mission by ID", () => {
    const id = store.createMission({ name: "Test", goal: "Do something" });
    const mission = store.getMission(id);
    expect(mission).not.toBeNull();
    expect(mission!.name).toBe("Test");
    expect(mission!.status).toBe("active");
  });

  it("lists missions with optional status filter", () => {
    store.createMission({ name: "A", goal: "g1" });
    store.createMission({ name: "B", goal: "g2" });
    expect(store.listMissions().length).toBe(2);
    expect(store.listMissions("active").length).toBe(2);
    expect(store.listMissions("completed").length).toBe(0);
  });

  it("updates mission status", () => {
    const id = store.createMission({ name: "Test", goal: "g" });
    store.updateMissionStatus(id, "paused");
    expect(store.getMission(id)!.status).toBe("paused");
  });

  it("adds steps to a mission", () => {
    const mId = store.createMission({ name: "Test", goal: "g" });
    const sId = store.addStep(mId, { description: "Step 1" });
    expect(sId).toBeDefined();
    const steps = store.getSteps(mId);
    expect(steps.length).toBe(1);
    expect(steps[0].description).toBe("Step 1");
  });

  it("updates step status", () => {
    const mId = store.createMission({ name: "Test", goal: "g" });
    const sId = store.addStep(mId, { description: "Step 1" });
    store.updateStepStatus(sId, "completed", "Done successfully");
    const steps = store.getSteps(mId);
    expect(steps[0].status).toBe("completed");
    expect(steps[0].result).toBe("Done successfully");
  });

  it("rejects step statuses outside the exported contract", () => {
    const mId = store.createMission({ name: "Test", goal: "g" });
    const sId = store.addStep(mId, { description: "Step 1" });

    expect(() => {
      (store.updateStepStatus as (id: string, status: string, result?: string) => void)(sId, "banana");
    }).toThrow();

    const steps = store.getSteps(mId);
    expect(steps[0].status).toBe("completed");
  });

  it("records verification results", () => {
    const mId = store.createMission({ name: "Test", goal: "g" });
    store.recordVerification(mId, { passed: false, reason: "Tests failing" });
    store.recordVerification(mId, { passed: true, reason: "All tests pass" });
    const verifications = store.getVerifications(mId);
    expect(verifications.length).toBe(2);
    expect(verifications[1].passed).toBe(true);
  });

  it("persists budget tracking", () => {
    const id = store.createMission({
      name: "Test",
      goal: "g",
      budget: { maxSteps: 20, maxCostUsd: 5.0 },
    });
    const mission = store.getMission(id);
    expect(mission!.budget).toBeDefined();
    expect(mission!.budget!.maxSteps).toBe(20);
  });

  it("getMission returns null for unknown ID", () => {
    expect(store.getMission("nonexistent")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// MissionManager — lifecycle orchestration
// ---------------------------------------------------------------------------

describe("MissionManager", () => {
  let dir: string;

  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("creates and retrieves a mission", async () => {
    const { MissionManager } = await import("../src/mission/manager.js");
    const manager = new MissionManager(join(dir, "test.db"));

    const id = manager.create({ name: "Ship feature", goal: "Implement login" });
    const mission = manager.get(id);
    expect(mission).not.toBeNull();
    expect(mission!.status).toBe("active");
  });

  it("advances mission with a new step", async () => {
    const { MissionManager } = await import("../src/mission/manager.js");
    const manager = new MissionManager(join(dir, "test.db"));

    const id = manager.create({ name: "Test", goal: "g" });
    manager.advance(id, "Created migration file");
    manager.advance(id, "Wrote unit tests");

    const steps = manager.steps(id);
    expect(steps.length).toBe(2);
    expect(steps[0].description).toBe("Created migration file");
  });

  it("verify returns verifier result and updates mission", async () => {
    const { MissionManager } = await import("../src/mission/manager.js");
    const manager = new MissionManager(join(dir, "test.db"));

    const id = manager.create({ name: "Test", goal: "g" });

    // Register a verifier that always passes
    manager.setVerifier(id, async () => ({
      passed: true,
      reason: "All checks pass",
    }));

    const result = await manager.verify(id);
    expect(result.passed).toBe(true);
    expect(manager.get(id)!.status).toBe("completed");
  });

  it("verify with failing verifier keeps mission active", async () => {
    const { MissionManager } = await import("../src/mission/manager.js");
    const manager = new MissionManager(join(dir, "test.db"));

    const id = manager.create({ name: "Test", goal: "g" });
    manager.setVerifier(id, async () => ({
      passed: false,
      reason: "Tests still failing",
      suggestions: ["Fix the type error"],
    }));

    const result = await manager.verify(id);
    expect(result.passed).toBe(false);
    expect(manager.get(id)!.status).toBe("active");
  });

  it("pause and resume lifecycle", async () => {
    const { MissionManager } = await import("../src/mission/manager.js");
    const manager = new MissionManager(join(dir, "test.db"));

    const id = manager.create({ name: "Test", goal: "g" });
    manager.pause(id);
    expect(manager.get(id)!.status).toBe("paused");

    manager.resume(id);
    expect(manager.get(id)!.status).toBe("active");
  });

  it("resume clears terminal completion metadata", async () => {
    const { MissionManager } = await import("../src/mission/manager.js");
    const manager = new MissionManager(join(dir, "test.db"));

    const id = manager.create({ name: "Test", goal: "g" });
    manager.cancel(id);
    expect(manager.get(id)!.completedAt).toBeDefined();

    manager.resume(id);
    const mission = manager.get(id)!;
    expect(mission.status).toBe("active");
    expect(mission.completedAt).toBeUndefined();
  });

  it("cancel sets status to canceled", async () => {
    const { MissionManager } = await import("../src/mission/manager.js");
    const manager = new MissionManager(join(dir, "test.db"));

    const id = manager.create({ name: "Test", goal: "g" });
    manager.cancel(id);
    expect(manager.get(id)!.status).toBe("canceled");
  });

  it("records verifier exceptions as failed verification attempts", async () => {
    const { MissionManager } = await import("../src/mission/manager.js");
    const { MissionStore } = await import("../src/mission/store.js");
    const manager = new MissionManager(join(dir, "test.db"));

    const id = manager.create({ name: "Test", goal: "g" });
    manager.setVerifier(id, async () => {
      throw new Error("Verifier transport failed");
    });

    const result = await manager.verify(id);
    expect(result.passed).toBe(false);
    expect(result.reason).toContain("Verifier error: Verifier transport failed");
    expect(result.metadata?.verifierThrew).toBe(true);
    expect(manager.get(id)!.status).toBe("active");

    manager.close();
    const store = new MissionStore(join(dir, "test.db"));
    const verifications = store.getVerifications(id);
    expect(verifications).toHaveLength(1);
    expect(verifications[0].passed).toBe(false);
    expect(verifications[0].reason).toContain("Verifier error: Verifier transport failed");
    store.close();
  });

  it("list returns missions with optional status filter", async () => {
    const { MissionManager } = await import("../src/mission/manager.js");
    const manager = new MissionManager(join(dir, "test.db"));

    manager.create({ name: "A", goal: "g1" });
    const bId = manager.create({ name: "B", goal: "g2" });
    manager.pause(bId);

    expect(manager.list().length).toBe(2);
    expect(manager.list("active").length).toBe(1);
    expect(manager.list("paused").length).toBe(1);
  });
});
