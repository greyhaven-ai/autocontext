/**
 * AC-467: Dashboard removed — server is API-only.
 *
 * These tests verify that the server no longer serves HTML dashboards
 * and instead returns useful API info at the root endpoint.
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
  return mkdtempSync(join(tmpdir(), "ac-467-no-dash-"));
}

async function createTestServer(dir: string) {
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
  const server = new InteractiveServer({ runManager: mgr, port: 0 });
  await server.start();
  return { server, mgr, url: server.url };
}

describe("API-only server (AC-467)", () => {
  let dir: string;
  let server: Awaited<ReturnType<typeof createTestServer>>["server"];
  let httpUrl: string;

  beforeEach(async () => {
    dir = makeTempDir();
    const ctx = await createTestServer(dir);
    server = ctx.server;
    httpUrl = `http://localhost:${server.port}`;
  });
  afterEach(async () => {
    await server.stop();
    rmSync(dir, { recursive: true, force: true });
  });

  it("GET / returns JSON API info, not HTML", async () => {
    const res = await fetch(`${httpUrl}/`);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.service).toBe("autocontext");
    expect(body.endpoints).toBeDefined();
  });

  it("GET /health still works", async () => {
    const res = await fetch(`${httpUrl}/health`);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.status).toBe("ok");
  });

  it("GET /api/runs still works", async () => {
    const res = await fetch(`${httpUrl}/api/runs`);
    expect(res.status).toBe(200);
  });

  it("GET /dashboard returns API info JSON (no HTML)", async () => {
    const res = await fetch(`${httpUrl}/dashboard`);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.service).toBe("autocontext");
  });
});
