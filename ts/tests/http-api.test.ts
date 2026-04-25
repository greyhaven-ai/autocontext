/**
 * Tests for AC-364: HTTP dashboard and REST API endpoints.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
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

async function postJson(url: string, body: Record<string, unknown>): Promise<{ status: number; body: unknown }> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return { status: res.status, body: await res.json() };
}

function readStringProperty(value: unknown, key: string): string {
  if (value === null || typeof value !== "object") {
    throw new Error(`expected response body to be an object with ${key}`);
  }
  const descriptor = Object.getOwnPropertyDescriptor(value, key);
  if (typeof descriptor?.value !== "string") {
    throw new Error(`expected response body field ${key} to be a string`);
  }
  return descriptor.value;
}

async function putJson(url: string, body: Record<string, unknown>): Promise<{ status: number; body: unknown }> {
  const res = await fetch(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return { status: res.status, body: await res.json() };
}

async function patchJson(url: string, body: Record<string, unknown>): Promise<{ status: number; body: unknown }> {
  const res = await fetch(url, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return { status: res.status, body: await res.json() };
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

  const scenarioKnowledgeDir = join(dir, "knowledge", "grid_ctf");
  mkdirSync(scenarioKnowledgeDir, { recursive: true });
  writeFileSync(
    join(scenarioKnowledgeDir, "playbook.md"),
    [
      "# Grid CTF Playbook",
      "",
      "<!-- LESSONS_START -->",
      "- Hold the center route.",
      "<!-- LESSONS_END -->",
      "",
      "<!-- COMPETITOR_HINTS_START -->",
      "Use measured aggression around the flag.",
      "<!-- COMPETITOR_HINTS_END -->",
    ].join("\n"),
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
    expect((body as Record<string, unknown>).status).toBe("ok");
  });

  it("GET / returns API info JSON (AC-467: dashboard removed)", async () => {
    const { status, body } = await fetchJson(`${baseUrl}/`);
    expect(status).toBe(200);
    expect((body as Record<string, unknown>).service).toBe("autocontext");
    const endpoints = (body as Record<string, unknown>).endpoints as Record<string, unknown>;
    expect(endpoints).toBeDefined();
    expect(endpoints.capabilities).toMatchObject({
      http: "/api/capabilities/http",
    });
    expect(endpoints.monitors).toBe("/api/monitors");
    expect(endpoints.notebooks).toBe("/api/notebooks");
    expect(endpoints.openclaw).toBe("/api/openclaw");
    expect(endpoints.cockpit).toBe("/api/cockpit");
    expect(endpoints.knowledge).toMatchObject({
      scenarios: "/api/knowledge/scenarios",
      export: "/api/knowledge/export/:scenario",
      import: "/api/knowledge/import",
      search: "/api/knowledge/search",
      solve: "/api/knowledge/solve",
      playbook: "/api/knowledge/playbook/:scenario",
    });
  });

  it("GET /api/capabilities/http returns the runtime parity matrix", async () => {
    const { status, body } = await fetchJson(`${baseUrl}/api/capabilities/http`);
    expect(status).toBe(200);
    const matrix = body as {
      version: number;
      summary: Record<string, number>;
      routes: Array<Record<string, unknown>>;
    };
    const routeFor = (method: string, path: string) =>
      matrix.routes.find((route) => route.method === method && route.path === path);
    expect(matrix.version).toBe(1);
    expect(matrix.summary.aligned).toBeGreaterThan(0);
    expect(matrix.summary.typescript_gap).toBeGreaterThan(0);
    expect(matrix.summary.python_gap).toBeGreaterThan(0);
    expect(routeFor("GET", "/")).toMatchObject({
      status: "aligned",
      python: { support: "supported" },
      typescript: { support: "supported" },
    });
    expect(routeFor("GET", "/dashboard")).toMatchObject({
      status: "aligned",
      python: { support: "supported" },
      typescript: { support: "supported" },
    });
    expect(matrix.routes).toContainEqual(expect.objectContaining({
      method: "POST",
      path: "/api/knowledge/import",
      status: "aligned",
    }));
    expect(matrix.routes).toContainEqual(expect.objectContaining({
      method: "GET",
      path: "/api/knowledge/playbook/:scenario",
      status: "python_gap",
    }));
    expect(matrix.routes).toContainEqual(expect.objectContaining({
      method: "GET",
      path: "/api/notebooks",
      status: "aligned",
    }));
    expect(matrix.routes).toContainEqual(expect.objectContaining({
      method: "GET",
      path: "/api/monitors",
      status: "aligned",
    }));
    expect(matrix.routes).toContainEqual(expect.objectContaining({
      method: "GET",
      path: "/api/openclaw/capabilities",
      status: "aligned",
    }));
    expect(matrix.routes).toContainEqual(expect.objectContaining({
      method: "POST",
      path: "/api/openclaw/evaluate",
      status: "aligned",
    }));
    expect(matrix.routes).toContainEqual(expect.objectContaining({
      method: "GET",
      path: "/api/cockpit/runs",
      status: "aligned",
    }));
    expect(matrix.routes).toContainEqual(expect.objectContaining({
      method: "GET",
      path: "/api/missions",
      status: "python_gap",
    }));
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
// Notebook endpoints
// ---------------------------------------------------------------------------

describe("HTTP API — notebooks", () => {
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

  it("GET /api/notebooks lists notebooks", async () => {
    const { status, body } = await fetchJson(`${baseUrl}/api/notebooks`);
    expect(status).toBe(200);
    expect(body).toEqual([]);
  });

  it("PUT /api/notebooks/:session_id creates and syncs a notebook", async () => {
    const { status, body } = await putJson(`${baseUrl}/api/notebooks/session-1`, {
      scenario_name: "grid_ctf",
      current_objective: "Hold the center route.",
      current_hypotheses: ["Center pressure improves capture odds."],
      best_run_id: "test-run-1",
      best_generation: 1,
      best_score: 0.7,
      unresolved_questions: ["Does flank pressure help?"],
      operator_observations: ["Blue team favored center."],
      follow_ups: ["Try a lower-risk opening."],
    });

    expect(status).toBe(200);
    expect(body).toMatchObject({
      session_id: "session-1",
      scenario_name: "grid_ctf",
      current_objective: "Hold the center route.",
      current_hypotheses: ["Center pressure improves capture odds."],
      best_run_id: "test-run-1",
      best_generation: 1,
      best_score: 0.7,
      unresolved_questions: ["Does flank pressure help?"],
      operator_observations: ["Blue team favored center."],
      follow_ups: ["Try a lower-risk opening."],
    });
    const notebookPath = join(dir, "runs", "sessions", "session-1", "notebook.json");
    expect(JSON.parse(readFileSync(notebookPath, "utf-8"))).toMatchObject({
      session_id: "session-1",
      scenario_name: "grid_ctf",
    });
    const eventLog = readFileSync(join(dir, "runs", "_interactive", "events.ndjson"), "utf-8");
    expect(eventLog).toContain("notebook_updated");
  });

  it("PUT /api/notebooks/:session_id merges partial updates", async () => {
    await putJson(`${baseUrl}/api/notebooks/session-1`, {
      scenario_name: "grid_ctf",
      current_objective: "First objective.",
      current_hypotheses: ["Keep this."],
    });

    const { status, body } = await putJson(`${baseUrl}/api/notebooks/session-1`, {
      current_objective: "Updated objective.",
    });

    expect(status).toBe(200);
    expect(body).toMatchObject({
      scenario_name: "grid_ctf",
      current_objective: "Updated objective.",
      current_hypotheses: ["Keep this."],
    });
  });

  it("PUT /api/notebooks/:session_id requires scenario_name for new notebooks", async () => {
    const { status, body } = await putJson(`${baseUrl}/api/notebooks/session-2`, {
      current_objective: "Missing scenario.",
    });

    expect(status).toBe(400);
    expect((body as Record<string, unknown>).detail).toContain("scenario_name");
  });

  it("PUT /api/notebooks/:session_id rejects decoded path traversal", async () => {
    const encodedTraversal = encodeURIComponent("../../escaped");

    const { status, body } = await putJson(`${baseUrl}/api/notebooks/${encodedTraversal}`, {
      scenario_name: "grid_ctf",
      current_objective: "Do not write outside the sessions root.",
    });

    expect(status).toBe(422);
    expect((body as Record<string, unknown>).detail).toContain("session_id");
    expect(existsSync(join(dir, "escaped", "notebook.json"))).toBe(false);
    expect(existsSync(join(dir, "runs", "escaped", "notebook.json"))).toBe(false);
  });

  it("GET /api/notebooks/:session_id returns 404 for missing notebooks", async () => {
    const { status, body } = await fetchJson(`${baseUrl}/api/notebooks/missing`);
    expect(status).toBe(404);
    expect((body as Record<string, unknown>).detail).toContain("Notebook not found");
  });

  it("DELETE /api/notebooks/:session_id deletes the notebook and artifact", async () => {
    await putJson(`${baseUrl}/api/notebooks/session-1`, {
      scenario_name: "grid_ctf",
      current_objective: "Delete this.",
    });
    const notebookPath = join(dir, "runs", "sessions", "session-1", "notebook.json");
    expect(existsSync(notebookPath)).toBe(true);

    const res = await fetch(`${baseUrl}/api/notebooks/session-1`, { method: "DELETE" });
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body).toEqual({ status: "deleted", session_id: "session-1" });
    expect(existsSync(notebookPath)).toBe(false);
    const eventLog = readFileSync(join(dir, "runs", "_interactive", "events.ndjson"), "utf-8");
    expect(eventLog).toContain("notebook_deleted");
  });
});

// ---------------------------------------------------------------------------
// Monitor endpoints
// ---------------------------------------------------------------------------

describe("HTTP API — monitors", () => {
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

  it("POST /api/monitors creates a monitor condition", async () => {
    const { status, body } = await postJson(`${baseUrl}/api/monitors`, {
      name: "Score floor",
      condition_type: "metric_threshold",
      params: { metric: "best_score", threshold: 0.8, direction: "above" },
      scope: "grid_ctf",
    });

    expect(status).toBe(201);
    expect(body).toMatchObject({
      name: "Score floor",
      condition_type: "metric_threshold",
      params: { metric: "best_score", threshold: 0.8, direction: "above" },
      scope: "grid_ctf",
      active: 1,
    });
    expect(typeof (body as Record<string, unknown>).id).toBe("string");
  });

  it("POST /api/monitors adds the default heartbeat timeout", async () => {
    const { status, body } = await postJson(`${baseUrl}/api/monitors`, {
      name: "Heartbeat",
      condition_type: "heartbeat_lost",
      params: {},
    });

    expect(status).toBe(201);
    expect(body).toMatchObject({
      params: {
        timeout_seconds: 300,
      },
    });
  });

  it("POST /api/monitors honors configured monitor limits and defaults", async () => {
    const previousMaxConditions = process.env.AUTOCONTEXT_MONITOR_MAX_CONDITIONS;
    const previousHeartbeatTimeout = process.env.AUTOCONTEXT_MONITOR_HEARTBEAT_TIMEOUT;
    process.env.AUTOCONTEXT_MONITOR_MAX_CONDITIONS = "1";
    process.env.AUTOCONTEXT_MONITOR_HEARTBEAT_TIMEOUT = "12";
    try {
      const first = await postJson(`${baseUrl}/api/monitors`, {
        name: "Configured heartbeat",
        condition_type: "heartbeat_lost",
        params: {},
      });
      expect(first.status).toBe(201);
      expect(first.body).toMatchObject({
        params: {
          timeout_seconds: 12,
        },
      });

      const second = await postJson(`${baseUrl}/api/monitors`, {
        name: "Over limit",
        condition_type: "process_exit",
        params: {},
      });
      expect(second.status).toBe(409);
      expect(second.body).toMatchObject({
        detail: expect.stringContaining("maximum active monitor conditions reached (1)"),
      });
    } finally {
      if (previousMaxConditions === undefined) {
        delete process.env.AUTOCONTEXT_MONITOR_MAX_CONDITIONS;
      } else {
        process.env.AUTOCONTEXT_MONITOR_MAX_CONDITIONS = previousMaxConditions;
      }
      if (previousHeartbeatTimeout === undefined) {
        delete process.env.AUTOCONTEXT_MONITOR_HEARTBEAT_TIMEOUT;
      } else {
        process.env.AUTOCONTEXT_MONITOR_HEARTBEAT_TIMEOUT = previousHeartbeatTimeout;
      }
    }
  });

  it("GET /api/monitors lists active conditions and supports active_only=false", async () => {
    const created = await postJson(`${baseUrl}/api/monitors`, {
      name: "Exit",
      condition_type: "process_exit",
      params: {},
    });
    const conditionId = readStringProperty(created.body, "id");
    await fetch(`${baseUrl}/api/monitors/${conditionId}`, { method: "DELETE" });

    const active = await fetchJson(`${baseUrl}/api/monitors`);
    expect(active.body).toEqual([]);

    const all = await fetchJson(`${baseUrl}/api/monitors?active_only=false`);
    expect(all.body).toContainEqual(expect.objectContaining({
      id: conditionId,
      active: 0,
    }));
  });

  it("DELETE /api/monitors/:condition_id deactivates conditions", async () => {
    const created = await postJson(`${baseUrl}/api/monitors`, {
      name: "Artifact",
      condition_type: "artifact_created",
      params: { path: "playbook.md" },
    });
    const conditionId = readStringProperty(created.body, "id");

    const res = await fetch(`${baseUrl}/api/monitors/${conditionId}`, { method: "DELETE" });

    expect(res.status).toBe(204);
    const missing = await fetch(`${baseUrl}/api/monitors/not-real`, { method: "DELETE" });
    expect(missing.status).toBe(404);
  });

  it("GET /api/monitors/alerts lists alerts", async () => {
    const { status, body } = await fetchJson(`${baseUrl}/api/monitors/alerts`);
    expect(status).toBe(200);
    expect(body).toEqual([]);
  });

  it("POST /api/monitors/:condition_id/wait returns fired alerts", async () => {
    const created = await postJson(`${baseUrl}/api/monitors`, {
      name: "Score crossed",
      condition_type: "metric_threshold",
      params: { metric: "best_score", threshold: 0.8, direction: "above" },
      scope: "run:test-run-1",
    });
    const conditionId = readStringProperty(created.body, "id");

    mgr.events.emit("generation_completed", {
      run_id: "test-run-1",
      best_score: 0.91,
    });

    const { status, body } = await postJson(`${baseUrl}/api/monitors/${conditionId}/wait?timeout=0.1`, {});
    expect(status).toBe(200);
    expect(body).toMatchObject({
      fired: true,
      alert: {
        condition_id: conditionId,
        condition_name: "Score crossed",
        condition_type: "metric_threshold",
      },
    });
  });

  it("POST /api/monitors rejects invalid condition types", async () => {
    const { status, body } = await postJson(`${baseUrl}/api/monitors`, {
      name: "Bad",
      condition_type: "unknown",
      params: {},
    });

    expect(status).toBe(409);
    expect((body as Record<string, unknown>).detail).toContain("invalid monitor condition type");
  });
});

// ---------------------------------------------------------------------------
// Cockpit endpoints
// ---------------------------------------------------------------------------

describe("HTTP API — cockpit", () => {
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

  it("mirrors notebook CRUD under /api/cockpit/notebooks", async () => {
    const created = await putJson(`${baseUrl}/api/cockpit/notebooks/test-run-1`, {
      scenario_name: "grid_ctf",
      current_objective: "Keep center control.",
      current_hypotheses: ["Center control raises capture odds."],
      best_score: 0.1,
      unresolved_questions: ["Does flank pressure matter?"],
      operator_observations: ["Prior run preferred middle lanes."],
      follow_ups: ["Try a higher path bias."],
    });

    expect(created.status).toBe(200);
    expect(created.body).toMatchObject({
      session_id: "test-run-1",
      scenario_name: "grid_ctf",
      current_objective: "Keep center control.",
    });

    const fetched = await fetchJson(`${baseUrl}/api/cockpit/notebooks/test-run-1`);
    expect(fetched.status).toBe(200);
    expect(fetched.body).toMatchObject({ session_id: "test-run-1" });

    const listed = await fetchJson(`${baseUrl}/api/cockpit/notebooks`);
    expect(listed.status).toBe(200);
    expect(listed.body).toContainEqual(expect.objectContaining({ session_id: "test-run-1" }));

    const effective = await fetchJson(`${baseUrl}/api/cockpit/notebooks/test-run-1/effective-context`);
    expect(effective.status).toBe(200);
    expect(effective.body).toMatchObject({
      session_id: "test-run-1",
      role_contexts: expect.objectContaining({
        competitor: expect.stringContaining("Keep center control."),
      }),
      warnings: [expect.objectContaining({
        field: "best_score",
        warning_type: "stale_score",
      })],
      notebook_empty: false,
    });

    const deleted = await fetch(`${baseUrl}/api/cockpit/notebooks/test-run-1`, { method: "DELETE" });
    expect(deleted.status).toBe(200);
  });

  it("GET /api/cockpit/runs returns cockpit run summaries", async () => {
    const { status, body } = await fetchJson(`${baseUrl}/api/cockpit/runs`);

    expect(status).toBe(200);
    expect(body).toContainEqual(expect.objectContaining({
      run_id: "test-run-1",
      scenario_name: "grid_ctf",
      generations_completed: 1,
      best_score: 0.7,
      best_elo: 1050,
      status: "running",
    }));
  });

  it("GET /api/cockpit/runs/:run_id/status returns detailed generation state", async () => {
    const { status, body } = await fetchJson(`${baseUrl}/api/cockpit/runs/test-run-1/status`);

    expect(status).toBe(200);
    expect(body).toMatchObject({
      run_id: "test-run-1",
      scenario_name: "grid_ctf",
      target_generations: 3,
      status: "running",
      generations: [expect.objectContaining({
        generation: 1,
        best_score: 0.7,
        elo: 1050,
      })],
    });
  });

  it("GET /api/cockpit/runs/:run_id/compare/:gen_a/:gen_b compares generations", async () => {
    const { SQLiteStore } = await import("../src/storage/index.js");
    const store = new SQLiteStore(join(dir, "test.db"));
    store.upsertGeneration("test-run-1", 2, {
      meanScore: 0.72,
      bestScore: 0.78,
      elo: 1105,
      wins: 4,
      losses: 1,
      gateDecision: "advance",
      status: "completed",
    });
    store.close();

    const { status, body } = await fetchJson(`${baseUrl}/api/cockpit/runs/test-run-1/compare/1/2`);

    expect(status).toBe(200);
    expect(body).toMatchObject({
      gen_a: expect.objectContaining({ generation: 1, best_score: 0.7 }),
      gen_b: expect.objectContaining({ generation: 2, best_score: 0.78 }),
      score_delta: 0.08,
      elo_delta: 55,
    });
  });

  it("GET /api/cockpit/runs/:run_id/resume returns resume affordances", async () => {
    const { status, body } = await fetchJson(`${baseUrl}/api/cockpit/runs/test-run-1/resume`);

    expect(status).toBe(200);
    expect(body).toMatchObject({
      run_id: "test-run-1",
      status: "running",
      last_generation: 1,
      can_resume: true,
    });
    expect((body as Record<string, unknown>).resume_hint).toContain("generation 2");
  });

  it("GET /api/cockpit/writeup/:run_id returns a markdown writeup", async () => {
    const { status, body } = await fetchJson(`${baseUrl}/api/cockpit/writeup/test-run-1`);

    expect(status).toBe(200);
    expect(body).toMatchObject({
      run_id: "test-run-1",
      scenario_name: "grid_ctf",
    });
    expect((body as Record<string, unknown>).writeup_markdown).toContain("test-run-1");
  });

  it("GET /api/cockpit/runs/:run_id/changelog returns generation deltas", async () => {
    const { status, body } = await fetchJson(`${baseUrl}/api/cockpit/runs/test-run-1/changelog`);

    expect(status).toBe(200);
    expect(body).toMatchObject({
      run_id: "test-run-1",
      changes: [],
    });
  });

  it("consultation routes are explicit when the TS consultation backend is unavailable", async () => {
    const consultation = await postJson(`${baseUrl}/api/cockpit/runs/test-run-1/consult`, {
      context_summary: "Need another opinion.",
    });
    expect(consultation.status).toBe(400);
    expect((consultation.body as Record<string, unknown>).detail).toContain("Consultation is not enabled");

    const listed = await fetchJson(`${baseUrl}/api/cockpit/runs/test-run-1/consultations`);
    expect(listed.status).toBe(200);
    expect(listed.body).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// OpenClaw endpoints
// ---------------------------------------------------------------------------

describe("HTTP API — OpenClaw", () => {
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

  it("POST /api/openclaw/evaluate scores a built-in game strategy", async () => {
    const { status, body } = await postJson(`${baseUrl}/api/openclaw/evaluate`, {
      scenario_name: "grid_ctf",
      strategy: { aggression: 0.6, defense: 0.4, path_bias: 0.7 },
      num_matches: 2,
      seed_base: 42,
    });

    expect(status).toBe(200);
    expect(body).toMatchObject({
      scenario: "grid_ctf",
      matches: 2,
    });
    expect((body as Record<string, unknown>).scores).toHaveLength(2);
    expect(typeof (body as Record<string, unknown>).mean_score).toBe("number");
    expect(typeof (body as Record<string, unknown>).best_score).toBe("number");
  });

  it("POST /api/openclaw/validate returns harness-compatible validation shape", async () => {
    const { status, body } = await postJson(`${baseUrl}/api/openclaw/validate`, {
      scenario_name: "grid_ctf",
      strategy: { aggression: 0.6, defense: 0.4, path_bias: 0.7 },
    });

    expect(status).toBe(200);
    expect(body).toMatchObject({
      valid: true,
      reason: "ok",
      scenario: "grid_ctf",
      harness_loaded: [],
      harness_passed: true,
      harness_errors: [],
    });
  });

  it("POST /api/openclaw/validate reports invalid strategies without transport failure", async () => {
    const { status, body } = await postJson(`${baseUrl}/api/openclaw/validate`, {
      scenario_name: "grid_ctf",
      strategy: { aggression: 0.9, defense: 0.8, path_bias: 0.7 },
    });

    expect(status).toBe(200);
    expect(body).toMatchObject({
      valid: false,
      reason: expect.stringContaining("combined aggression"),
      scenario: "grid_ctf",
      harness_passed: false,
      harness_errors: [expect.stringContaining("combined aggression")],
    });
  });

  it("POST /api/openclaw/validate returns 400 for unknown scenarios", async () => {
    const { status, body } = await postJson(`${baseUrl}/api/openclaw/validate`, {
      scenario_name: "not_real",
      strategy: {},
    });

    expect(status).toBe(400);
    expect((body as Record<string, unknown>).detail).toContain("Unknown scenario");
  });

  it("POST /api/openclaw/artifacts publishes and lists artifacts", async () => {
    const artifact = {
      id: "artifact-1",
      name: "Grid policy",
      artifact_type: "policy",
      scenario: "grid_ctf",
      version: 1,
      provenance: {
        run_id: "test-run-1",
        generation: 1,
        scenario: "grid_ctf",
        settings: {},
      },
      source_code: "def strategy(state):\n    return {'aggression': 0.6}\n",
      tags: ["smoke"],
      created_at: "2026-04-25T00:00:00Z",
    };

    const published = await postJson(`${baseUrl}/api/openclaw/artifacts`, artifact);
    expect(published.status).toBe(200);
    expect(published.body).toMatchObject({
      status: "published",
      artifact_id: "artifact-1",
      artifact_type: "policy",
    });

    const listed = await fetchJson(`${baseUrl}/api/openclaw/artifacts?scenario=grid_ctf&artifact_type=policy`);
    expect(listed.status).toBe(200);
    expect(listed.body).toContainEqual(expect.objectContaining({
      id: "artifact-1",
      name: "Grid policy",
      artifact_type: "policy",
      scenario: "grid_ctf",
      version: 1,
    }));

    const fetched = await fetchJson(`${baseUrl}/api/openclaw/artifacts/artifact-1`);
    expect(fetched.status).toBe(200);
    expect(fetched.body).toMatchObject(artifact);
  });

  it("POST /api/openclaw/artifacts rejects malformed policy artifacts", async () => {
    const { status, body } = await postJson(`${baseUrl}/api/openclaw/artifacts`, {
      id: "artifact-missing-source",
      name: "Grid policy",
      artifact_type: "policy",
      scenario: "grid_ctf",
      version: 1,
      provenance: {
        run_id: "test-run-1",
        generation: 1,
        scenario: "grid_ctf",
        settings: {},
      },
    });

    expect(status).toBe(400);
    expect((body as Record<string, unknown>).detail).toContain("source_code");
  });

  it("POST /api/openclaw/artifacts rejects scenario traversal before harness writes", async () => {
    const { status, body } = await postJson(`${baseUrl}/api/openclaw/artifacts`, {
      id: "harness-escape",
      name: "Escaping harness",
      artifact_type: "harness",
      scenario: "../outside",
      version: 1,
      provenance: {
        run_id: "test-run-1",
        generation: 1,
        scenario: "../outside",
        settings: {},
      },
      source_code: "def validate(state, strategy):\n    return True\n",
    });

    expect(status).toBe(400);
    expect((body as Record<string, unknown>).detail).toContain("scenario");
    expect(existsSync(join(dir, "outside", "harness"))).toBe(false);
  });

  it("GET /api/openclaw/discovery endpoints advertise runtime and scenario state", async () => {
    const capabilities = await fetchJson(`${baseUrl}/api/openclaw/discovery/capabilities`);
    expect(capabilities.status).toBe(200);
    expect(capabilities.body).toMatchObject({
      version: "0.1.0",
      runtime_health: expect.objectContaining({
        executor_mode: expect.any(String),
        agent_provider: expect.any(String),
      }),
      scenario_capabilities: expect.objectContaining({
        grid_ctf: expect.objectContaining({
          scenario_name: "grid_ctf",
          evaluation_mode: "tournament",
          has_playbook: true,
        }),
      }),
    });

    const scenario = await fetchJson(`${baseUrl}/api/openclaw/discovery/scenario/grid_ctf`);
    expect(scenario.status).toBe(200);
    expect(scenario.body).toMatchObject({
      scenario_name: "grid_ctf",
      evaluation_mode: "tournament",
      has_playbook: true,
      best_score: 0.7,
      best_elo: 1050,
    });

    const health = await fetchJson(`${baseUrl}/api/openclaw/discovery/health`);
    expect(health.status).toBe(200);
    expect(health.body).toMatchObject({
      executor_mode: expect.any(String),
      openclaw_runtime_kind: "factory",
      openclaw_compatibility_version: "1.0",
    });
  });

  it("GET /api/openclaw/skill/manifest returns a ClawHub manifest", async () => {
    const { status, body } = await fetchJson(`${baseUrl}/api/openclaw/skill/manifest`);

    expect(status).toBe(200);
    expect(body).toMatchObject({
      name: "autocontext",
      rest_base_path: "/api/openclaw",
    });
    expect((body as Record<string, unknown>).scenarios).toContainEqual(expect.objectContaining({
      name: "grid_ctf",
      display_name: "Grid Ctf",
      scenario_type: "parametric",
    }));
  });

  it("distillation job endpoints keep Python-compatible lifecycle semantics", async () => {
    const triggered = await postJson(`${baseUrl}/api/openclaw/distill`, {
      scenario: "grid_ctf",
      source_artifact_ids: ["artifact-1"],
      training_config: { epochs: 1 },
    });

    expect(triggered.status).toBe(400);
    expect(triggered.body).toMatchObject({
      status: "failed",
      scenario: "grid_ctf",
    });
    expect((triggered.body as Record<string, unknown>).error).toContain("No distillation sidecar configured");
    const jobId = (triggered.body as Record<string, unknown>).job_id as string;

    const job = await fetchJson(`${baseUrl}/api/openclaw/distill/${jobId}`);
    expect(job.status).toBe(200);
    expect(job.body).toMatchObject({
      job_id: jobId,
      status: "failed",
      scenario: "grid_ctf",
    });

    const status = await fetchJson(`${baseUrl}/api/openclaw/distill?scenario=grid_ctf`);
    expect(status.status).toBe(200);
    expect(status.body).toMatchObject({
      active_jobs: 0,
      jobs: [expect.objectContaining({ job_id: jobId })],
    });
  });

  it("PATCH /api/openclaw/distill/:job_id rejects invalid transitions", async () => {
    const triggered = await postJson(`${baseUrl}/api/openclaw/distill`, {
      scenario: "grid_ctf",
    });
    const jobId = (triggered.body as Record<string, unknown>).job_id as string;

    const updated = await patchJson(`${baseUrl}/api/openclaw/distill/${jobId}`, {
      status: "completed",
      result_artifact_id: "artifact-1",
    });

    expect(updated.status).toBe(400);
    expect((updated.body as Record<string, unknown>).detail).toContain("Invalid transition");
  });

  it("GET /api/openclaw/artifacts/:artifact_id returns 404 for unknown artifacts", async () => {
    const { status, body } = await fetchJson(`${baseUrl}/api/openclaw/artifacts/not-real`);
    expect(status).toBe(404);
    expect((body as Record<string, unknown>).detail).toContain("not found");
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

  it("GET /api/knowledge/scenarios lists solved knowledge", async () => {
    const { status, body } = await fetchJson(`${baseUrl}/api/knowledge/scenarios`);
    expect(status).toBe(200);
    expect(body).toContainEqual({ scenario: "grid_ctf", hasPlaybook: true });
  });

  it("GET /api/knowledge/export/:scenario exports a skill package", async () => {
    const { status, body } = await fetchJson(`${baseUrl}/api/knowledge/export/grid_ctf`);
    expect(status).toBe(200);
    const data = body as Record<string, unknown>;
    expect(data.scenario_name).toBe("grid_ctf");
    expect(data.skill_markdown).toContain("Grid CTF");
    expect(data.suggested_filename).toBe("grid-ctf-knowledge.md");
  });

  it("GET /api/knowledge/export/:scenario rejects decoded path traversal", async () => {
    const outsideDir = join(dir, "outside");
    mkdirSync(outsideDir, { recursive: true });
    writeFileSync(join(outsideDir, "playbook.md"), "# Outside\n\nshould not export", "utf-8");

    const { status, body } = await fetchJson(
      `${baseUrl}/api/knowledge/export/${encodeURIComponent("../outside")}`,
    );

    expect(status).toBe(422);
    expect((body as Record<string, unknown>).error).toContain("Invalid scenario");
  });

  it("POST /api/knowledge/import imports a strategy package", async () => {
    const { status, body } = await postJson(`${baseUrl}/api/knowledge/import`, {
      package: {
        scenario_name: "imported_task",
        display_name: "Imported Task",
        description: "A package imported over the REST API.",
        playbook: "# Imported Task\n\nUse the imported strategy.",
        lessons: ["Prefer known-good imported strategy."],
        best_strategy: { answer: "imported" },
        best_score: 0.93,
        best_elo: 1510,
        hints: "Keep the imported hint close.",
        harness: {
          validator: "def validate():\n    return True\n",
        },
        metadata: {
          source: "http-test",
        },
        skill_markdown: "# Imported Skill\n\nUse the imported skill.",
      },
      conflict_policy: "overwrite",
    });

    expect(status).toBe(200);
    expect(body).toMatchObject({
      scenario: "imported_task",
      playbookWritten: true,
      harnessWritten: ["validator"],
      skillWritten: true,
      metadataWritten: true,
      conflictPolicy: "overwrite",
    });
    expect(readFileSync(join(dir, "knowledge", "imported_task", "playbook.md"), "utf-8"))
      .toContain("Use the imported strategy.");
    expect(readFileSync(
      join(dir, "knowledge", "imported_task", "package_metadata.json"),
      "utf-8",
    )).toContain("http-test");
    expect(existsSync(join(dir, "skills", "imported-task-ops", "SKILL.md"))).toBe(true);
  });

  it("POST /api/knowledge/import rejects unknown conflict policies", async () => {
    const { status, body } = await postJson(`${baseUrl}/api/knowledge/import`, {
      package: { scenario_name: "imported_task" },
      conflict_policy: "replace",
    });

    expect(status).toBe(422);
    expect((body as Record<string, unknown>).detail).toContain("conflict_policy");
  });

  it("POST /api/knowledge/search finds prior strategy text", async () => {
    const { status, body } = await postJson(`${baseUrl}/api/knowledge/search`, {
      query: "aggression",
      top_k: 3,
    });
    expect(status).toBe(200);
    const results = body as Array<Record<string, unknown>>;
    expect(results[0]).toMatchObject({
      scenario: "grid_ctf",
      display_name: "Grid Ctf",
      best_score: 0.7,
    });
  });

  it("POST /api/knowledge/solve submits a solve job", async () => {
    const { status, body } = await postJson(`${baseUrl}/api/knowledge/solve`, {
      description: "solve grid ctf",
      generations: 1,
    });
    expect(status).toBe(200);
    expect(body).toMatchObject({ status: "pending" });
    expect(typeof (body as Record<string, unknown>).job_id).toBe("string");
  });

  it("GET /api/knowledge/solve/:jobId returns 404 for missing jobs", async () => {
    const { status, body } = await fetchJson(`${baseUrl}/api/knowledge/solve/not-real`);
    expect(status).toBe(404);
    expect((body as Record<string, unknown>).detail).toContain("not found");
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
