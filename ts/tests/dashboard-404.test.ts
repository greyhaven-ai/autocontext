/**
 * Tests for AC-417: Dashboard 404 in published npm package.
 *
 * Verifies that the dashboard route works when the dashboard file exists
 * (monorepo or bundled npm) and fails gracefully when it doesn't.
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
  return mkdtempSync(join(tmpdir(), "ac-dashboard-"));
}

async function createTestServer(dir: string, dashboardDirOverride?: string) {
  const { RunManager, InteractiveServer } = await import("../src/server/index.js");
  const { SQLiteStore } = await import("../src/storage/index.js");

  const dbPath = join(dir, "test.db");
  const store = new SQLiteStore(dbPath);
  store.migrate(join(__dirname, "..", "migrations"));
  store.close();

  const mgr = new RunManager({
    dbPath,
    migrationsDir: join(__dirname, "..", "migrations"),
    runsRoot: join(dir, "runs"),
    knowledgeRoot: join(dir, "knowledge"),
    providerType: "deterministic",
  });
  const server = new InteractiveServer({
    runManager: mgr,
    port: 0,
    dashboardDirOverride,
  });
  await server.start();
  return { server, baseUrl: `http://localhost:${server.port}` };
}

describe("Dashboard route", () => {
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

  it("GET / serves bundled dashboard when available", async () => {
    const res = await fetch(`${baseUrl}/`);
    expect(res.status).toBe(200);
    const body = await res.text();
    expect(body).toContain("html");
    expect(body).toContain("autocontext Dashboard");
  });

  it("GET / and /dashboard/* return a helpful fallback when dashboard files are unavailable", async () => {
    await server.stop();
    const missingDashboardDir = join(dir, "missing-dashboard");
    const restarted = await createTestServer(dir, missingDashboardDir);
    server = restarted.server;
    baseUrl = restarted.baseUrl;

    const rootRes = await fetch(`${baseUrl}/`);
    expect(rootRes.status).toBe(404);
    const rootBody = await rootRes.json();
    expect(rootBody.message).toContain("Dashboard files not found");
    expect(rootBody.api.runs).toBe("/api/runs");

    const dashboardRes = await fetch(`${baseUrl}/dashboard/index.html`);
    expect(dashboardRes.status).toBe(404);
    const dashboardBody = await dashboardRes.json();
    expect(dashboardBody.message).toContain("Dashboard files not found");
    expect(dashboardBody.api.websocket).toBe("/ws/interactive");
  });

  it("GET /health still works when dashboard files are unavailable", async () => {
    await server.stop();
    const restarted = await createTestServer(dir, join(dir, "missing-dashboard"));
    server = restarted.server;
    baseUrl = restarted.baseUrl;

    const res = await fetch(`${baseUrl}/health`);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.ok).toBe(true);
  });

  it("GET /api/runs still works when dashboard files are unavailable", async () => {
    await server.stop();
    const restarted = await createTestServer(dir, join(dir, "missing-dashboard"));
    server = restarted.server;
    baseUrl = restarted.baseUrl;

    const res = await fetch(`${baseUrl}/api/runs`);
    expect(res.status).toBe(200);
  });
});
