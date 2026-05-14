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
  const progressDir = join(scenarioKnowledgeDir, "progress_reports");
  mkdirSync(progressDir, { recursive: true });
  writeFileSync(
    join(progressDir, "test-run-1.json"),
    JSON.stringify({
      run_id: "test-run-1",
      scenario: "grid_ctf",
      total_generations: 1,
      advances: 1,
      rollbacks: 0,
      retries: 0,
      progress: {
        raw_score: 0.7,
        normalized_score: 0.7,
        score_floor: 0,
        score_ceiling: 1,
        pct_of_ceiling: 70,
      },
      cost: {
        total_input_tokens: 20000,
        total_output_tokens: 10000,
        total_tokens: 30000,
        total_cost_usd: 0.15,
      },
    }, null, 2),
    "utf-8",
  );
  const weaknessDir = join(scenarioKnowledgeDir, "weakness_reports");
  mkdirSync(weaknessDir, { recursive: true });
  writeFileSync(
    join(weaknessDir, "test-run-1.json"),
    JSON.stringify({
      run_id: "test-run-1",
      scenario: "grid_ctf",
      total_generations: 1,
      weaknesses: [{
        category: "validation_failure",
        severity: "medium",
        affected_generations: [1],
        description: "Parse failure on generation 1",
        evidence: { count: 1 },
        frequency: 1,
      }],
    }, null, 2),
    "utf-8",
  );
  const facetDir = join(dir, "knowledge", "analytics", "facets");
  mkdirSync(facetDir, { recursive: true });
  writeFileSync(
    join(facetDir, "test-run-1.json"),
    JSON.stringify({
      run_id: "test-run-1",
      scenario: "grid_ctf",
      scenario_family: "game",
      agent_provider: "deterministic",
      executor_mode: "local",
      total_generations: 1,
      advances: 1,
      retries: 0,
      rollbacks: 0,
      best_score: 0.7,
      best_elo: 1050,
      total_duration_seconds: 12,
      total_tokens: 30000,
      total_cost_usd: 0.15,
      tool_invocations: 2,
      validation_failures: 1,
      consultation_count: 0,
      consultation_cost_usd: 0,
      friction_signals: [{
        signal_type: "validation_failure",
        severity: "medium",
        generation_index: 1,
        description: "Parse failure on generation 1",
        evidence: ["ev-1"],
        recoverable: true,
      }],
      delight_signals: [{
        signal_type: "strong_improvement",
        generation_index: 1,
        description: "Center route improved quickly",
        evidence: ["ev-2"],
      }],
      events: [],
      metadata: {},
      created_at: "2026-04-25T00:00:00Z",
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

async function persistRuntimeSession(dir: string): Promise<void> {
  const {
    RuntimeSessionEventLog,
    RuntimeSessionEventStore,
    RuntimeSessionEventType,
  } = await import("../src/session/runtime-events.js");
  const eventStore = new RuntimeSessionEventStore(join(dir, "test.db"));
  const log = RuntimeSessionEventLog.create({
    sessionId: "run:test-run-1:runtime",
    metadata: { goal: "autoctx run grid_ctf", runId: "test-run-1" },
  });
  log.append(RuntimeSessionEventType.PROMPT_SUBMITTED, {
    role: "architect",
    prompt: "Improve the grid strategy",
  });
  log.append(RuntimeSessionEventType.ASSISTANT_MESSAGE, {
    role: "architect",
    text: "Try measured aggression around the flag.",
  });
  eventStore.save(log);
  eventStore.close();
}

function expectTestRunRuntimeSessionDiscovery(value: unknown): void {
  expect(value).toMatchObject({
    runtime_session: expect.objectContaining({
      session_id: "run:test-run-1:runtime",
      event_count: 2,
    }),
    runtime_session_url: "/api/cockpit/runs/test-run-1/runtime-session",
  });
}

function persistContextSelectionDecision(dir: string): void {
  const contextDir = join(dir, "runs", "test-run-1", "context_selection");
  mkdirSync(contextDir, { recursive: true });
  writeFileSync(
    join(contextDir, "gen_1_generation_prompt_context.json"),
    JSON.stringify({
      schema_version: 1,
      run_id: "test-run-1",
      scenario_name: "grid_ctf",
      generation: 1,
      stage: "generation_prompt_context",
      created_at: "2026-01-02T03:04:05.000Z",
      metadata: {
        context_budget_telemetry: {
          input_token_estimate: 120,
          output_token_estimate: 20,
          dedupe_hit_count: 1,
          component_cap_hit_count: 2,
          trimmed_component_count: 1,
        },
        prompt_compaction_cache: {
          hits: 0,
          misses: 10,
          lookups: 10,
        },
      },
      metrics: {
        candidate_count: 1,
        selected_count: 1,
        candidate_token_estimate: 100,
        selected_token_estimate: 20,
      },
      candidates: [{
        artifact_id: "playbook",
        artifact_type: "prompt_component",
        source: "prompt_assembly",
        candidate_token_estimate: 100,
        selected_token_estimate: 20,
        selected: true,
        selection_reason: "retained_after_prompt_assembly",
        candidate_content_hash: "candidate",
        selected_content_hash: "selected",
      }],
    }, null, 2),
    "utf-8",
  );
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
    expect(endpoints.context_selection).toBe("/api/cockpit/runs/:run_id/context-selection");
    expect(endpoints.hub).toBe("/api/hub");
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
    expect(matrix.summary.typescript_gap).toBeGreaterThanOrEqual(0);
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
      path: "/api/cockpit/runs/:run_id/context-selection",
      status: "aligned",
    }));
    expect(matrix.routes).toContainEqual(expect.objectContaining({
      method: "GET",
      path: "/api/cockpit/runtime-sessions",
      status: "python_gap",
    }));
    expect(matrix.routes).toContainEqual(expect.objectContaining({
      method: "GET",
      path: "/api/cockpit/runs/:run_id/runtime-session",
      status: "python_gap",
    }));
    expect(matrix.routes).toContainEqual(expect.objectContaining({
      method: "GET",
      path: "/api/cockpit/runs/:run_id/runtime-session/timeline",
      status: "python_gap",
    }));
    expect(matrix.routes).toContainEqual(expect.objectContaining({
      method: "GET",
      path: "/api/hub/feed",
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
      runtime_session: null,
      runtime_session_url: "/api/cockpit/runs/test-run-1/runtime-session",
    }));
  });

  it("GET /api/cockpit/runs includes runtime-session summaries when present", async () => {
    await persistRuntimeSession(dir);

    const { status, body } = await fetchJson(`${baseUrl}/api/cockpit/runs`);

    expect(status).toBe(200);
    const runs = body as Array<Record<string, unknown>>;
    const run = runs.find((item) => item.run_id === "test-run-1");
    expectTestRunRuntimeSessionDiscovery(run);
  });

  it("GET /api/cockpit/runs/:run_id/status returns detailed generation state", async () => {
    await persistRuntimeSession(dir);

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
    expectTestRunRuntimeSessionDiscovery(body);
  });

  it("GET /api/cockpit/runs/:run_id/context-selection returns telemetry cards", async () => {
    persistContextSelectionDecision(dir);

    const { status, body } = await fetchJson(`${baseUrl}/api/cockpit/runs/test-run-1/context-selection`);

    expect(status).toBe(200);
    expect(body).toMatchObject({
      status: "completed",
      run_id: "test-run-1",
      scenario_name: "grid_ctf",
      summary: expect.objectContaining({
        budget_token_reduction: 100,
        compaction_cache_hit_rate: 0,
      }),
      telemetry_cards: expect.arrayContaining([
        expect.objectContaining({
          key: "context_budget",
          severity: "warning",
          value: "100 est. tokens reduced",
        }),
        expect.objectContaining({
          key: "semantic_compaction_cache",
          severity: "warning",
          value: "0.0% hit rate",
        }),
      ]),
    });
  });

  it("GET /api/cockpit/runs/:run_id/context-selection handles missing artifacts", async () => {
    const { status, body } = await fetchJson(`${baseUrl}/api/cockpit/runs/test-run-1/context-selection`);

    expect(status).toBe(404);
    expect(body).toMatchObject({
      detail: expect.stringContaining("No context selection artifacts"),
    });
  });

  it("GET /api/cockpit/runs/:run_id/context-selection rejects escaped run ids", async () => {
    const { status, body } = await fetchJson(`${baseUrl}/api/cockpit/runs/%2E%2E%2Foutside/context-selection`);

    expect(status).toBe(422);
    expect(body).toMatchObject({
      detail: expect.stringContaining("escapes runs root"),
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
    await persistRuntimeSession(dir);

    const { status, body } = await fetchJson(`${baseUrl}/api/cockpit/runs/test-run-1/resume`);

    expect(status).toBe(200);
    expect(body).toMatchObject({
      run_id: "test-run-1",
      status: "running",
      last_generation: 1,
      can_resume: true,
    });
    expect((body as Record<string, unknown>).resume_hint).toContain("generation 2");
    expectTestRunRuntimeSessionDiscovery(body);
  });

  it("GET /api/cockpit/writeup/:run_id returns a markdown writeup", async () => {
    const { status, body } = await fetchJson(`${baseUrl}/api/cockpit/writeup/test-run-1`);

    expect(status).toBe(200);
    expect(body).toMatchObject({
      run_id: "test-run-1",
      scenario_name: "grid_ctf",
    });
    const writeup = readStringProperty(body, "writeup_markdown");
    expect(writeup).toContain("test-run-1");
    expect(writeup).toContain("## Playbook");
    expect(writeup).toContain("Hold the center route.");
  });

  it("GET /api/cockpit/writeup/:run_id prefers persisted trace writeups", async () => {
    const writeupsDir = join(dir, "knowledge", "analytics", "writeups");
    mkdirSync(writeupsDir, { recursive: true });
    writeFileSync(
      join(writeupsDir, "trace-writeup-test-run-1.json"),
      JSON.stringify({
        writeup_id: "trace-writeup-test-run-1",
        run_id: "test-run-1",
        generation_index: 1,
        findings: [],
        failure_motifs: [],
        recovery_paths: [],
        summary: "Persisted trace-grounded summary.",
        created_at: "2025-01-01T00:00:00.000Z",
        metadata: {
          scenario: "grid_ctf",
          scenario_family: "game",
        },
      }, null, 2),
      "utf-8",
    );

    const { status, body } = await fetchJson(`${baseUrl}/api/cockpit/writeup/test-run-1`);

    expect(status).toBe(200);
    const writeup = readStringProperty(body, "writeup_markdown");
    expect(writeup).toContain("## Trace Summary");
    expect(writeup).toContain("Persisted trace-grounded summary.");
    expect(writeup).not.toContain("## Playbook");
  });

  it("GET /api/cockpit/runs/:run_id/changelog returns generation deltas", async () => {
    const { status, body } = await fetchJson(`${baseUrl}/api/cockpit/runs/test-run-1/changelog`);

    expect(status).toBe(200);
    expect(body).toMatchObject({
      run_id: "test-run-1",
      generations: [
        {
          generation: 1,
          score_delta: 0.7,
          elo_delta: 50,
          gate_decision: "advance",
          new_tools: [],
          playbook_changed: false,
        },
      ],
    });
  });

  it("GET /api/cockpit/runtime-sessions lists recorded provider-runtime logs", async () => {
    await persistRuntimeSession(dir);

    const { status, body } = await fetchJson(`${baseUrl}/api/cockpit/runtime-sessions?limit=5`);

    expect(status).toBe(200);
    expect(body).toEqual({
      sessions: [
        expect.objectContaining({
          session_id: "run:test-run-1:runtime",
          goal: "autoctx run grid_ctf",
          event_count: 2,
        }),
      ],
    });
  });

  it("GET /api/cockpit/runtime-sessions/:session_id returns a recorded event log", async () => {
    await persistRuntimeSession(dir);

    const { status, body } = await fetchJson(
      `${baseUrl}/api/cockpit/runtime-sessions/${encodeURIComponent("run:test-run-1:runtime")}`,
    );

    expect(status).toBe(200);
    expect(body).toMatchObject({
      sessionId: "run:test-run-1:runtime",
      metadata: { goal: "autoctx run grid_ctf", runId: "test-run-1" },
      events: [
        expect.objectContaining({
          eventType: "prompt_submitted",
          payload: {
            role: "architect",
            prompt: "Improve the grid strategy",
          },
        }),
        expect.objectContaining({
          eventType: "assistant_message",
          payload: {
            role: "architect",
            text: "Try measured aggression around the flag.",
          },
        }),
      ],
    });
  });

  it("GET /api/cockpit/runtime-sessions/:session_id/timeline returns an operator timeline", async () => {
    await persistRuntimeSession(dir);

    const { status, body } = await fetchJson(
      `${baseUrl}/api/cockpit/runtime-sessions/${encodeURIComponent("run:test-run-1:runtime")}/timeline`,
    );

    expect(status).toBe(200);
    expect(body).toMatchObject({
      summary: {
        session_id: "run:test-run-1:runtime",
        event_count: 2,
      },
      item_count: 1,
      items: [
        expect.objectContaining({
          kind: "prompt",
          status: "completed",
          role: "architect",
          prompt_preview: "Improve the grid strategy",
          response_preview: "Try measured aggression around the flag.",
        }),
      ],
    });
  });

  it("GET /api/cockpit/runs/:run_id/runtime-session resolves the run-scoped log", async () => {
    await persistRuntimeSession(dir);

    const { status, body } = await fetchJson(`${baseUrl}/api/cockpit/runs/test-run-1/runtime-session`);

    expect(status).toBe(200);
    expect(body).toMatchObject({
      sessionId: "run:test-run-1:runtime",
      metadata: { runId: "test-run-1" },
    });
  });

  it("GET /api/cockpit/runs/:run_id/runtime-session/timeline resolves the run-scoped timeline", async () => {
    await persistRuntimeSession(dir);

    const { status, body } = await fetchJson(
      `${baseUrl}/api/cockpit/runs/test-run-1/runtime-session/timeline`,
    );

    expect(status).toBe(200);
    expect(body).toMatchObject({
      summary: { session_id: "run:test-run-1:runtime" },
      items: [
        expect.objectContaining({
          kind: "prompt",
          status: "completed",
        }),
      ],
    });
  });

  it("GET /api/cockpit/runs/:run_id/runtime-session returns 404 when no log exists", async () => {
    const { status, body } = await fetchJson(`${baseUrl}/api/cockpit/runs/test-run-1/runtime-session`);

    expect(status).toBe(404);
    expect(body).toEqual({
      detail: "Runtime session for run 'test-run-1' not found",
      session_id: "run:test-run-1:runtime",
    });
  });

  it("POST /api/cockpit/runs/:run_id/consult persists a settings-backed advisory", async () => {
    const savedEnv = {
      enabled: process.env.AUTOCONTEXT_CONSULTATION_ENABLED,
      provider: process.env.AUTOCONTEXT_CONSULTATION_PROVIDER,
      model: process.env.AUTOCONTEXT_CONSULTATION_MODEL,
    };
    process.env.AUTOCONTEXT_CONSULTATION_ENABLED = "true";
    process.env.AUTOCONTEXT_CONSULTATION_PROVIDER = "deterministic";
    process.env.AUTOCONTEXT_CONSULTATION_MODEL = "deterministic-dev";

    try {
      const consultation = await postJson(`${baseUrl}/api/cockpit/runs/test-run-1/consult`, {
        context_summary: "Need another opinion.",
      });
      expect(consultation.status).toBe(200);
      expect(consultation.body).toMatchObject({
        run_id: "test-run-1",
        generation: 1,
        trigger: "operator_request",
        model_used: "deterministic-dev",
      });
      expect(readStringProperty(consultation.body, "advisory_markdown")).toContain("Consultation model");

      const listed = await fetchJson(`${baseUrl}/api/cockpit/runs/test-run-1/consultations`);
      expect(listed.status).toBe(200);
      expect(listed.body).toEqual([
        expect.objectContaining({
          run_id: "test-run-1",
          generation_index: 1,
          trigger: "operator_request",
          context_summary: "Need another opinion.",
          model_used: "deterministic-dev",
        }),
      ]);
      expect(
        existsSync(join(dir, "runs", "test-run-1", "generations", "gen_1", "consultation.md")),
      ).toBe(true);
    } finally {
      if (savedEnv.enabled === undefined) delete process.env.AUTOCONTEXT_CONSULTATION_ENABLED;
      else process.env.AUTOCONTEXT_CONSULTATION_ENABLED = savedEnv.enabled;
      if (savedEnv.provider === undefined) delete process.env.AUTOCONTEXT_CONSULTATION_PROVIDER;
      else process.env.AUTOCONTEXT_CONSULTATION_PROVIDER = savedEnv.provider;
      if (savedEnv.model === undefined) delete process.env.AUTOCONTEXT_CONSULTATION_MODEL;
      else process.env.AUTOCONTEXT_CONSULTATION_MODEL = savedEnv.model;
    }
  });
});

// ---------------------------------------------------------------------------
// Research hub endpoints
// ---------------------------------------------------------------------------

describe("HTTP API — research hub", () => {
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

  it("upserts, lists, fetches, and heartbeats hub sessions", async () => {
    const created = await putJson(`${baseUrl}/api/hub/sessions/session-1`, {
      scenario_name: "grid_ctf",
      current_objective: "Coordinate shared research.",
      current_hypotheses: ["Center control is still promising."],
      owner: "operator",
      status: "active",
      shared: true,
      metadata: { channel: "ci" },
    });

    expect(created.status).toBe(200);
    expect(created.body).toMatchObject({
      session_id: "session-1",
      scenario_name: "grid_ctf",
      owner: "operator",
      shared: true,
      metadata: { channel: "ci" },
      artifact_path: expect.stringContaining("notebook.json"),
    });

    const listed = await fetchJson(`${baseUrl}/api/hub/sessions`);
    expect(listed.status).toBe(200);
    expect(listed.body).toContainEqual(expect.objectContaining({ session_id: "session-1" }));

    const fetched = await fetchJson(`${baseUrl}/api/hub/sessions/session-1`);
    expect(fetched.status).toBe(200);
    expect(fetched.body).toMatchObject({ session_id: "session-1", owner: "operator" });

    const heartbeat = await postJson(`${baseUrl}/api/hub/sessions/session-1/heartbeat`, {
      lease_seconds: 60,
    });
    expect(heartbeat.status).toBe(200);
    expect(heartbeat.body).toMatchObject({
      session_id: "session-1",
      lease_expires_at: expect.any(String),
      last_heartbeat_at: expect.any(String),
    });
  });

  it("rejects hub session ids that would escape notebook artifact storage", async () => {
    const escaped = await putJson(`${baseUrl}/api/hub/sessions/..%2F..%2Foutside`, {
      scenario_name: "grid_ctf",
      current_objective: "Do not write outside the session root.",
    });

    expect(escaped.status).toBe(422);
    expect((escaped.body as Record<string, unknown>).detail).toContain("invalid hub id");
    expect(existsSync(join(dir, "outside", "notebook.json"))).toBe(false);
  });

  it("promotes a run to a package and adopts it through the package importer", async () => {
    await putJson(`${baseUrl}/api/hub/sessions/session-1`, {
      scenario_name: "grid_ctf",
      current_hypotheses: ["Use measured aggression."],
    });

    const promoted = await postJson(`${baseUrl}/api/hub/packages/from-run/test-run-1`, {
      title: "Grid CTF shared package",
      session_id: "session-1",
      actor: "operator",
      compatibility_tags: ["grid_ctf", "ci"],
      adoption_notes: "Adopt after review.",
    });

    expect(promoted.status).toBe(200);
    expect(promoted.body).toMatchObject({
      scenario_name: "grid_ctf",
      source_run_id: "test-run-1",
      source_generation: 1,
      title: "Grid CTF shared package",
      best_score: 0.7,
      best_elo: 1050,
      strategy: { aggression: 0.6 },
      notebook_hypotheses: ["Use measured aggression."],
      compatibility_tags: ["grid_ctf", "ci"],
    });
    const packageId = (promoted.body as Record<string, unknown>).package_id as string;
    expect(packageId).toMatch(/^pkg-/);
    expect(existsSync(join(dir, "knowledge", "_hub", "packages", packageId, "shared_package.json"))).toBe(true);
    expect(existsSync(join(dir, "knowledge", "_hub", "packages", packageId, "strategy_package.json"))).toBe(true);

    const listed = await fetchJson(`${baseUrl}/api/hub/packages`);
    expect(listed.status).toBe(200);
    expect(listed.body).toContainEqual(expect.objectContaining({ package_id: packageId }));

    const fetched = await fetchJson(`${baseUrl}/api/hub/packages/${packageId}`);
    expect(fetched.status).toBe(200);
    expect(fetched.body).toMatchObject({ package_id: packageId, scenario_name: "grid_ctf" });

    const adopted = await postJson(`${baseUrl}/api/hub/packages/${packageId}/adopt`, {
      actor: "operator",
      conflict_policy: "merge",
    });
    expect(adopted.status).toBe(200);
    expect(adopted.body).toMatchObject({
      import_result: expect.objectContaining({
        scenario: "grid_ctf",
        conflictPolicy: "merge",
        metadataWritten: true,
      }),
      promotion_event: expect.objectContaining({
        package_id: packageId,
        action: "adopt",
      }),
    });
    expect(existsSync(join(dir, "skills", "grid-ctf-ops", "SKILL.md"))).toBe(true);
  });

  it("materializes run results, records promotions, and returns the hub feed", async () => {
    const result = await postJson(`${baseUrl}/api/hub/results/from-run/test-run-1`, {
      title: "Grid result",
    });
    expect(result.status).toBe(200);
    expect(result.body).toMatchObject({
      scenario_name: "grid_ctf",
      run_id: "test-run-1",
      title: "Grid result",
      best_score: 0.7,
      best_elo: 1050,
      normalized_progress: expect.stringContaining("70.00% of ceiling"),
      cost_summary: "$0.15 total, 30000 tokens",
      weakness_summary: expect.stringContaining("Parse failure"),
      friction_signals: ["Parse failure on generation 1"],
      delight_signals: ["Center route improved quickly"],
    });
    const resultId = (result.body as Record<string, unknown>).result_id as string;
    expect(resultId).toMatch(/^res-/);

    const listedResults = await fetchJson(`${baseUrl}/api/hub/results`);
    expect(listedResults.status).toBe(200);
    expect(listedResults.body).toContainEqual(expect.objectContaining({ result_id: resultId }));

    const fetchedResult = await fetchJson(`${baseUrl}/api/hub/results/${resultId}`);
    expect(fetchedResult.status).toBe(200);
    expect(fetchedResult.body).toMatchObject({ result_id: resultId, summary: expect.stringContaining("test-run-1") });

    const promotion = await postJson(`${baseUrl}/api/hub/promotions`, {
      package_id: "pkg-external",
      source_run_id: "test-run-1",
      action: "label",
      actor: "operator",
      label: "recommended",
      metadata: { note: "manual label" },
    });
    expect(promotion.status).toBe(200);
    expect(promotion.body).toMatchObject({
      package_id: "pkg-external",
      action: "label",
      label: "recommended",
    });

    const feed = await fetchJson(`${baseUrl}/api/hub/feed`);
    expect(feed.status).toBe(200);
    expect(feed.body).toMatchObject({
      results: [expect.objectContaining({ result_id: resultId })],
      promotions: [expect.objectContaining({ package_id: "pkg-external" })],
    });
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

  it("streams runtime-session events over /ws/events", async () => {
    const { WebSocket } = await import("ws");
    const { createInMemoryWorkspaceEnv } = await import("../src/runtimes/workspace-env.js");
    const { RuntimeSession } = await import("../src/session/runtime-session.js");
    const { RuntimeSessionEventStore } = await import("../src/session/runtime-events.js");
    const { createRuntimeSessionEventStreamSink } =
      await import("../src/server/runtime-session-event-stream.js");
    const wsUrl = baseUrl.replace(/^http/, "ws") + "/ws/events";
    const eventStore = new RuntimeSessionEventStore(join(dir, "test.db"));

    try {
      const raw = await new Promise<string>((resolve, reject) => {
        const ws = new WebSocket(wsUrl);
        ws.once("open", () => {
          ws.once("message", (data) => {
            resolve(data.toString());
            ws.close();
          });
          ws.once("error", reject);

          const session = RuntimeSession.create({
            sessionId: "runtime-ws",
            goal: "ship live runtime visibility",
            workspace: createInMemoryWorkspaceEnv({ cwd: "/workspace" }),
            eventStore,
            eventSink: createRuntimeSessionEventStreamSink(mgr.events),
          });
          void session.submitPrompt({
            prompt: "Inspect live runtime state",
            role: "observer",
            handler: () => ({ text: "visible" }),
          });
        });
        ws.once("error", reject);
      });
      const payload = JSON.parse(raw) as Record<string, unknown>;
      expect(payload).toMatchObject({
        event: "runtime_session_event",
        channel: "runtime_session",
        v: 1,
        payload: {
          session_id: "runtime-ws",
          goal: "ship live runtime visibility",
          event_count: 1,
          event: {
            event_type: "prompt_submitted",
            sequence: 0,
            payload: {
              prompt: "Inspect live runtime state",
              role: "observer",
            },
          },
        },
      });
    } finally {
      eventStore.close();
    }
  }, 15000);
});
