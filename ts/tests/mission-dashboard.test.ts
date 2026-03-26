/**
 * Tests for AC-414: Mission dashboard API endpoints + event protocol.
 *
 * - REST: /api/missions, /api/missions/:id, /api/missions/:id/steps
 * - WebSocket: mission_progress event type
 * - MissionEventEmitter: emits events on state changes
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-dash-"));
}

// ---------------------------------------------------------------------------
// Mission event protocol types
// ---------------------------------------------------------------------------

describe("Mission event protocol", () => {
  it("MissionProgressMsgSchema validates progress events", async () => {
    const { MissionProgressMsgSchema } = await import("../src/server/protocol.js");
    const msg = MissionProgressMsgSchema.parse({
      type: "mission_progress",
      missionId: "mission-abc",
      status: "active",
      stepsCompleted: 3,
      latestStep: "Fixed type error",
    });
    expect(msg.missionId).toBe("mission-abc");
    expect(msg.stepsCompleted).toBe(3);
  });

  it("MissionProgressMsgSchema is in ServerMessageSchema", async () => {
    const { parseServerMessage } = await import("../src/server/protocol.js");
    expect(() => parseServerMessage({
      type: "mission_progress",
      missionId: "m-1",
      status: "active",
      stepsCompleted: 1,
    })).not.toThrow();
  });
});

// ---------------------------------------------------------------------------
// MissionEventEmitter
// ---------------------------------------------------------------------------

describe("MissionEventEmitter", () => {
  let dir: string;
  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("emits mission_created event", async () => {
    const { MissionEventEmitter } = await import("../src/mission/events.js");
    const { MissionManager } = await import("../src/mission/manager.js");
    const manager = new MissionManager(join(dir, "test.db"));
    const emitter = new MissionEventEmitter();

    const events: Array<Record<string, unknown>> = [];
    emitter.on("mission_created", (e) => events.push(e));

    const id = manager.create({ name: "Test", goal: "g" });
    emitter.emitCreated(id, "Test", "g");

    expect(events.length).toBe(1);
    expect(events[0].missionId).toBe(id);
    expect(events[0].name).toBe("Test");
    manager.close();
  });

  it("emits mission_step event", async () => {
    const { MissionEventEmitter } = await import("../src/mission/events.js");
    const emitter = new MissionEventEmitter();

    const events: Array<Record<string, unknown>> = [];
    emitter.on("mission_step", (e) => events.push(e));

    emitter.emitStep("m-1", "Wrote unit tests", 5);
    expect(events.length).toBe(1);
    expect(events[0].description).toBe("Wrote unit tests");
    expect(events[0].stepNumber).toBe(5);
  });

  it("emits mission_status_changed event", async () => {
    const { MissionEventEmitter } = await import("../src/mission/events.js");
    const emitter = new MissionEventEmitter();

    const events: Array<Record<string, unknown>> = [];
    emitter.on("mission_status_changed", (e) => events.push(e));

    emitter.emitStatusChange("m-1", "active", "completed");
    expect(events.length).toBe(1);
    expect(events[0].from).toBe("active");
    expect(events[0].to).toBe("completed");
  });

  it("emits mission_verified event", async () => {
    const { MissionEventEmitter } = await import("../src/mission/events.js");
    const emitter = new MissionEventEmitter();

    const events: Array<Record<string, unknown>> = [];
    emitter.on("mission_verified", (e) => events.push(e));

    emitter.emitVerified("m-1", true, "All tests pass");
    expect(events.length).toBe(1);
    expect(events[0].passed).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// REST API route builders
// ---------------------------------------------------------------------------

describe("Mission API routes", () => {
  let dir: string;
  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("buildMissionApiRoutes returns handlers for all endpoints", async () => {
    const { buildMissionApiRoutes } = await import("../src/server/mission-api.js");
    const { MissionManager } = await import("../src/mission/manager.js");
    const manager = new MissionManager(join(dir, "test.db"));

    const routes = buildMissionApiRoutes(manager);
    expect(routes.listMissions).toBeDefined();
    expect(routes.getMission).toBeDefined();
    expect(routes.getMissionSteps).toBeDefined();
    expect(routes.getMissionSubgoals).toBeDefined();
    expect(routes.getMissionBudget).toBeDefined();
    manager.close();
  });

  it("listMissions returns JSON array", async () => {
    const { buildMissionApiRoutes } = await import("../src/server/mission-api.js");
    const { MissionManager } = await import("../src/mission/manager.js");
    const manager = new MissionManager(join(dir, "test.db"));
    manager.create({ name: "A", goal: "g1" });
    manager.create({ name: "B", goal: "g2" });

    const routes = buildMissionApiRoutes(manager);
    const result = routes.listMissions();
    expect(result.length).toBe(2);
    manager.close();
  });

  it("getMission returns mission with step count", async () => {
    const { buildMissionApiRoutes } = await import("../src/server/mission-api.js");
    const { MissionManager } = await import("../src/mission/manager.js");
    const manager = new MissionManager(join(dir, "test.db"));
    const id = manager.create({ name: "Test", goal: "g" });
    manager.advance(id, "Step 1");

    const routes = buildMissionApiRoutes(manager);
    const result = routes.getMission(id);
    expect(result).not.toBeNull();
    expect(result!.name).toBe("Test");
    expect(result!.stepsCount).toBe(1);
    manager.close();
  });

  it("getMissionSteps returns step array", async () => {
    const { buildMissionApiRoutes } = await import("../src/server/mission-api.js");
    const { MissionManager } = await import("../src/mission/manager.js");
    const manager = new MissionManager(join(dir, "test.db"));
    const id = manager.create({ name: "Test", goal: "g" });
    manager.advance(id, "Step 1");
    manager.advance(id, "Step 2");

    const routes = buildMissionApiRoutes(manager);
    const steps = routes.getMissionSteps(id);
    expect(steps.length).toBe(2);
    manager.close();
  });

  it("getMissionBudget returns usage stats", async () => {
    const { buildMissionApiRoutes } = await import("../src/server/mission-api.js");
    const { MissionManager } = await import("../src/mission/manager.js");
    const manager = new MissionManager(join(dir, "test.db"));
    const id = manager.create({ name: "Test", goal: "g", budget: { maxSteps: 10 } });
    manager.advance(id, "Step 1");

    const routes = buildMissionApiRoutes(manager);
    const budget = routes.getMissionBudget(id);
    expect(budget.stepsUsed).toBe(1);
    expect(budget.maxSteps).toBe(10);
    expect(budget.exhausted).toBe(false);
    manager.close();
  });
});
