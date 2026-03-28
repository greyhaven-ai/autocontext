/**
 * Tests for AC-414: Mission dashboard API endpoints + event protocol.
 *
 * - REST: /api/missions, /api/missions/:id, /api/missions/:id/steps
 * - WebSocket: mission_progress event type
 * - MissionEventEmitter: emits events on state changes
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, mkdirSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-dash-"));
}

async function fetchJson(url: string, init?: RequestInit): Promise<{ status: number; body: unknown }> {
  const res = await fetch(url, init);
  const body = await res.json();
  return { status: res.status, body };
}

async function fetchText(url: string): Promise<{ status: number; body: string }> {
  const res = await fetch(url);
  const body = await res.text();
  return { status: res.status, body };
}

async function createMissionDashboardServer(dir: string) {
  const { RunManager, InteractiveServer } = await import("../src/server/index.js");
  const { MissionManager } = await import("../src/mission/manager.js");

  const dbPath = join(dir, "test.db");
  const runsRoot = join(dir, "runs");
  const knowledgeRoot = join(dir, "knowledge");
  mkdirSync(runsRoot, { recursive: true });
  mkdirSync(knowledgeRoot, { recursive: true });

  const seedMissionManager = new MissionManager(dbPath);
  const missionId = seedMissionManager.create({
    name: "Ship login",
    goal: "Implement OAuth without regressions",
    budget: { maxSteps: 5 },
  });
  seedMissionManager.advance(missionId, "Create mission verifier");
  seedMissionManager.close();

  const mgr = new RunManager({
    dbPath,
    migrationsDir: join(__dirname, "..", "migrations"),
    runsRoot,
    knowledgeRoot,
    providerType: "deterministic",
  });
  const server = new InteractiveServer({ runManager: mgr, port: 0 });
  await server.start();
  return { server, baseUrl: `http://localhost:${server.port}`, missionId };
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

    const routes = buildMissionApiRoutes(manager, join(dir, "runs"));
    expect(routes.listMissions).toBeDefined();
    expect(routes.getMission).toBeDefined();
    expect(routes.getMissionSteps).toBeDefined();
    expect(routes.getMissionSubgoals).toBeDefined();
    expect(routes.getMissionBudget).toBeDefined();
    expect(routes.getMissionArtifacts).toBeDefined();
    manager.close();
  });

  it("listMissions returns JSON array", async () => {
    const { buildMissionApiRoutes } = await import("../src/server/mission-api.js");
    const { MissionManager } = await import("../src/mission/manager.js");
    const manager = new MissionManager(join(dir, "test.db"));
    manager.create({ name: "A", goal: "g1" });
    manager.create({ name: "B", goal: "g2" });

    const routes = buildMissionApiRoutes(manager, join(dir, "runs"));
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

    const routes = buildMissionApiRoutes(manager, join(dir, "runs"));
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

    const routes = buildMissionApiRoutes(manager, join(dir, "runs"));
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

    const routes = buildMissionApiRoutes(manager, join(dir, "runs"));
    const budget = routes.getMissionBudget(id);
    expect(budget.stepsUsed).toBe(1);
    expect(budget.maxSteps).toBe(10);
    expect(budget.exhausted).toBe(false);
    manager.close();
  });
});

describe("Mission dashboard integration", () => {
  let dir: string;
  let server: Awaited<ReturnType<typeof createMissionDashboardServer>>["server"];
  let baseUrl: string;
  let missionId: string;

  beforeEach(async () => {
    dir = makeTempDir();
    const setup = await createMissionDashboardServer(dir);
    server = setup.server;
    baseUrl = setup.baseUrl;
    missionId = setup.missionId;
  });

  afterEach(async () => {
    await server.stop();
    rmSync(dir, { recursive: true, force: true });
  });

  it("mounts mission REST endpoints on the live server", async () => {
    const list = await fetchJson(`${baseUrl}/api/missions`);
    expect(list.status).toBe(200);
    expect((list.body as Array<Record<string, unknown>>)[0]?.id).toBe(missionId);

    const detail = await fetchJson(`${baseUrl}/api/missions/${missionId}`);
    expect(detail.status).toBe(200);
    expect((detail.body as Record<string, unknown>).stepsCount).toBe(1);
    expect((detail.body as Record<string, unknown>).budgetUsage).toBeDefined();

    const budget = await fetchJson(`${baseUrl}/api/missions/${missionId}/budget`);
    expect((budget.body as Record<string, unknown>).stepsUsed).toBe(1);

    const artifacts = await fetchJson(`${baseUrl}/api/missions/${missionId}/artifacts`);
    expect((artifacts.body as Record<string, unknown>).checkpointDir).toContain(`/missions/${missionId}/checkpoints`);
  });

  it("mission operator controls work against the live server and write checkpoints", async () => {
    const paused = await fetchJson(`${baseUrl}/api/missions/${missionId}/pause`, { method: "POST" });
    expect(paused.status).toBe(200);
    expect((paused.body as Record<string, unknown>).status).toBe("paused");

    const resumed = await fetchJson(`${baseUrl}/api/missions/${missionId}/resume`, { method: "POST" });
    expect((resumed.body as Record<string, unknown>).status).toBe("active");

    const advanced = await fetchJson(`${baseUrl}/api/missions/${missionId}/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ maxIterations: 1 }),
    });
    expect(advanced.status).toBe(200);
    expect((advanced.body as Record<string, unknown>).checkpointPath).toBeDefined();
    expect((advanced.body as Record<string, unknown>).planGenerated).toBe(true);
    expect((advanced.body as Record<string, unknown>).finalStatus).toBe("completed");

    const artifacts = await fetchJson(`${baseUrl}/api/missions/${missionId}/artifacts`);
    expect(Array.isArray((artifacts.body as Record<string, unknown>).checkpoints)).toBe(true);
    expect(((artifacts.body as Record<string, unknown>).checkpoints as unknown[]).length).toBeGreaterThan(0);
  });

  it("streams mission progress via WebSocket (AC-467: dashboard removed, API-only)", async () => {
    const apiInfo = await fetchJson(`${baseUrl}/`);
    expect(apiInfo.status).toBe(200);
    expect((apiInfo.body as Record<string, unknown>).service).toBe("autocontext");

    const { WebSocket } = await import("ws");
    const wsUrl = baseUrl.replace(/^http/, "ws") + "/ws/events";
    const raw = await new Promise<string>((resolve, reject) => {
      const ws = new WebSocket(wsUrl);
      ws.once("open", async () => {
        try {
          ws.once("message", (data) => {
            resolve(data.toString());
            ws.close();
          });
          await fetch(`${baseUrl}/api/missions/${missionId}/pause`, { method: "POST" });
        } catch (error) {
          reject(error);
        }
      });
      ws.once("error", reject);
    });

    const payload = JSON.parse(raw) as Record<string, unknown>;
    expect(payload.channel).toBe("mission");
    expect(payload.event).toBe("mission_progress");
    expect((payload.payload as Record<string, unknown>).missionId).toBe(missionId);
    expect((payload.payload as Record<string, unknown>).status).toBe("paused");
  }, 15000);
});
