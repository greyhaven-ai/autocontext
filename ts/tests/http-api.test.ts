/**
 * Tests for AC-364: HTTP dashboard and REST API endpoints.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { dirname } from "node:path";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-http-api-"));
}

async function fetchJson(url: string): Promise<{ status: number; body: unknown }> {
  const res = await fetch(url);
  const body = await res.json();
  return { status: res.status, body };
}

async function fetchText(url: string): Promise<{ status: number; body: string }> {
  const res = await fetch(url);
  const body = await res.text();
  return { status: res.status, body };
}

async function createTestServer(dir: string) {
  const { RunManager, InteractiveServer } = await import("../src/server/index.js");
  const { SQLiteStore } = await import("../src/storage/index.js");

  // Pre-populate with a run
  const dbPath = join(dir, "test.db");
  const store = new SQLiteStore(dbPath);
  store.migrate(join(__dirname, "..", "migrations"));
  store.createRun("test-run-1", "grid_ctf", 3, "local");
  store.upsertGeneration("test-run-1", 1, {
    meanScore: 0.65,
    bestScore: 0.70,
    elo: 1050,
    wins: 3,
    losses: 2,
    gateDecision: "advance",
    status: "completed",
  });
  store.recordMatch("test-run-1", 1, {
    seed: 42,
    score: 0.70,
    passedValidation: true,
    validationErrors: "",
    winner: "challenger",
  });
  store.appendAgentOutput("test-run-1", 1, "competitor", '{"aggression": 0.6}');
  store.close();

  const replayDir = join(dir, "runs", "test-run-1", "generations", "gen_1", "replays");
  mkdirSync(replayDir, { recursive: true });
  writeFileSync(
    join(replayDir, "grid_ctf_1.json"),
    JSON.stringify({
      scenario: "grid_ctf",
      seed: 42,
      narrative: "Blue team secured the center route.",
      timeline: [{ turn: 1, action: "advance" }],
      matches: [{ seed: 42, score: 0.7, winner: "challenger" }],
    }, null, 2),
    "utf-8",
  );

  const customDir = join(dir, "knowledge", "_custom_scenarios", "custom_agent_task");
  mkdirSync(customDir, { recursive: true });
  writeFileSync(
    join(customDir, "agent_task_spec.json"),
    JSON.stringify({
      task_prompt: "Summarize the control-plane state.",
      judge_rubric: "Prefer concise and accurate summaries.",
      output_format: "free_text",
      max_rounds: 1,
      quality_threshold: 0.9,
    }, null, 2),
    "utf-8",
  );

  const mgr = new RunManager({
    dbPath,
    migrationsDir: join(__dirname, "..", "migrations"),
    runsRoot: join(dir, "runs"),
    knowledgeRoot: join(dir, "knowledge"),
    providerType: "deterministic",
  });
  const server = new InteractiveServer({ runManager: mgr, port: 0 });
  await server.start();
  return { server, mgr, baseUrl: `http://localhost:${server.port}` };
}

// ---------------------------------------------------------------------------
// Health endpoint (already exists — regression check)
// ---------------------------------------------------------------------------

describe("HTTP API — health", () => {
  let dir: string;
  let server: Awaited<ReturnType<typeof createTestServer>>["server"];
  let baseUrl: string;

  beforeEach(async () => {
    dir = makeTempDir();
    const s = await createTestServer(dir);
    server = s.server;
    baseUrl = s.baseUrl;
  });

  afterEach(async () => {
    await server.stop();
    rmSync(dir, { recursive: true, force: true });
  });

  it("GET /health returns ok", async () => {
    const { status, body } = await fetchJson(`${baseUrl}/health`);
    expect(status).toBe(200);
    expect((body as Record<string, unknown>).ok).toBe(true);
  });

  it("GET / serves the dashboard HTML", async () => {
    const { status, body } = await fetchText(`${baseUrl}/`);
    expect(status).toBe(200);
    expect(body).toContain("<title>autocontext Dashboard</title>");
    expect(body).toContain("Live Events");
  });
});

// ---------------------------------------------------------------------------
// Run listing
// ---------------------------------------------------------------------------

describe("HTTP API — runs", () => {
  let dir: string;
  let server: Awaited<ReturnType<typeof createTestServer>>["server"];
  let baseUrl: string;

  beforeEach(async () => {
    dir = makeTempDir();
    const s = await createTestServer(dir);
    server = s.server;
    baseUrl = s.baseUrl;
  });

  afterEach(async () => {
    await server.stop();
    rmSync(dir, { recursive: true, force: true });
  });

  it("GET /api/runs returns run list", async () => {
    const { status, body } = await fetchJson(`${baseUrl}/api/runs`);
    expect(status).toBe(200);
    const runs = body as Array<Record<string, unknown>>;
    expect(runs.length).toBeGreaterThan(0);
    expect(runs[0].run_id).toBe("test-run-1");
  });

  it("GET /api/runs/:id/status returns generation details", async () => {
    const { status, body } = await fetchJson(`${baseUrl}/api/runs/test-run-1/status`);
    expect(status).toBe(200);
    const gens = body as Array<Record<string, unknown>>;
    expect(gens.length).toBe(1);
    expect(gens[0].best_score).toBeCloseTo(0.70);
  });

  it("GET /api/runs/:id/status returns 404 for missing run", async () => {
    const res = await fetch(`${baseUrl}/api/runs/nonexistent/status`);
    expect(res.status).toBe(404);
  });

  it("GET /api/runs/:id/replay/:gen returns persisted replay artifact", async () => {
    const { status, body } = await fetchJson(`${baseUrl}/api/runs/test-run-1/replay/1`);
    expect(status).toBe(200);
    const data = body as Record<string, unknown>;
    expect(data.scenario).toBe("grid_ctf");
    expect(data.narrative).toBe("Blue team secured the center route.");
    expect((data.timeline as unknown[]).length).toBe(1);
  });

  it("GET /api/runs/:id/replay/:gen returns 404 when replay artifact is missing", async () => {
    const res = await fetch(`${baseUrl}/api/runs/test-run-1/replay/99`);
    expect(res.status).toBe(404);
  });
});

// ---------------------------------------------------------------------------
// Knowledge endpoints
// ---------------------------------------------------------------------------

describe("HTTP API — knowledge", () => {
  let dir: string;
  let server: Awaited<ReturnType<typeof createTestServer>>["server"];
  let baseUrl: string;

  beforeEach(async () => {
    dir = makeTempDir();
    const s = await createTestServer(dir);
    server = s.server;
    baseUrl = s.baseUrl;
  });

  afterEach(async () => {
    await server.stop();
    rmSync(dir, { recursive: true, force: true });
  });

  it("GET /api/knowledge/playbook/:scenario returns playbook", async () => {
    const { status, body } = await fetchJson(`${baseUrl}/api/knowledge/playbook/grid_ctf`);
    expect(status).toBe(200);
    const data = body as Record<string, unknown>;
    expect(typeof data.content).toBe("string");
  });

  it("GET /api/scenarios returns scenario list", async () => {
    const { status, body } = await fetchJson(`${baseUrl}/api/scenarios`);
    expect(status).toBe(200);
    const scenarios = body as Array<Record<string, unknown>>;
    expect(scenarios.length).toBeGreaterThan(0);
    expect(scenarios.some((s) => s.name === "grid_ctf")).toBe(true);
    expect(scenarios.some((s) => s.name === "custom_agent_task")).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Dashboard event websocket
// ---------------------------------------------------------------------------

describe("HTTP API — dashboard event stream", () => {
  let dir: string;
  let server: Awaited<ReturnType<typeof createTestServer>>["server"];
  let mgr: Awaited<ReturnType<typeof createTestServer>>["mgr"];
  let baseUrl: string;

  beforeEach(async () => {
    dir = makeTempDir();
    const s = await createTestServer(dir);
    server = s.server;
    mgr = s.mgr;
    baseUrl = s.baseUrl;
  });

  afterEach(async () => {
    await server.stop();
    rmSync(dir, { recursive: true, force: true });
  });

  it("streams live events over /ws/events for the dashboard", async () => {
    const { WebSocket } = await import("ws");
    const wsUrl = baseUrl.replace(/^http/, "ws") + "/ws/events";

    const raw = await new Promise<string>((resolve, reject) => {
      const ws = new WebSocket(wsUrl);
      ws.once("open", () => {
        ws.once("message", (data) => {
          resolve(data.toString());
          ws.close();
        });
        ws.once("error", reject);

        const events = (mgr as unknown as {
          events: { emit: (event: string, payload: Record<string, unknown>) => void };
        }).events;
        events.emit("run_started", { run_id: "ws-test", scenario: "grid_ctf" });
      });
      ws.once("error", reject);
    });
    const payload = JSON.parse(raw) as Record<string, unknown>;
    expect(payload.event).toBe("run_started");
    expect(payload.v).toBe(1);
    expect(payload.channel).toBe("generation");
    expect((payload.payload as Record<string, unknown>).run_id).toBe("ws-test");
  }, 15000);
});
