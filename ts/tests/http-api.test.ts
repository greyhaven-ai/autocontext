/**
 * Tests for AC-364: HTTP dashboard and REST API endpoints.
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
  return mkdtempSync(join(tmpdir(), "ac-http-api-"));
}

async function fetchJson(url: string): Promise<{ status: number; body: unknown }> {
  const res = await fetch(url);
  const body = await res.json();
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

  it("GET /api/runs/:id/replay/:gen returns matches and outputs", async () => {
    const { status, body } = await fetchJson(`${baseUrl}/api/runs/test-run-1/replay/1`);
    expect(status).toBe(200);
    const data = body as Record<string, unknown>;
    expect((data.matches as unknown[]).length).toBe(1);
    expect((data.agent_outputs as unknown[]).length).toBe(1);
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
  });
});
