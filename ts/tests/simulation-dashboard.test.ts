/**
 * AC-449: Simulation dashboard — API routes + visualization data.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import {
  mkdtempSync,
  mkdirSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { dirname, join } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import {
  buildSimulationApiRoutes,
} from "../src/server/simulation-api.js";

let tmpDir: string;
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "ac449-test-"));
});
afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

function writeSimReport(
  name: string,
  data: Record<string, unknown>,
  root: string = tmpDir,
): void {
  const dir = join(root, "_simulations", name);
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "report.json"), JSON.stringify(data, null, 2));
}

// ---------------------------------------------------------------------------
// API routes
// ---------------------------------------------------------------------------

describe("Simulation API routes", () => {
  it("listSimulations returns empty for no artifacts", () => {
    const routes = buildSimulationApiRoutes(tmpDir);
    expect(routes.listSimulations()).toEqual([]);
  });

  it("listSimulations discovers saved simulations", () => {
    writeSimReport("deploy_sim", {
      id: "sim_001",
      name: "deploy_sim",
      family: "simulation",
      status: "completed",
      summary: { score: 0.85 },
    });
    writeSimReport("pricing_sim", {
      id: "sim_002",
      name: "pricing_sim",
      family: "simulation",
      status: "completed",
      summary: { score: 0.72 },
    });
    const routes = buildSimulationApiRoutes(tmpDir);
    const list = routes.listSimulations();
    expect(list.length).toBe(2);
    expect(list.some((s) => s.name === "deploy_sim")).toBe(true);
  });

  it("getSimulation returns full report", () => {
    writeSimReport("test_sim", {
      id: "sim_001",
      name: "test_sim",
      family: "simulation",
      status: "completed",
      summary: { score: 0.85, reasoning: "Good", dimensionScores: {} },
      assumptions: ["stable network"],
      warnings: [],
    });
    const routes = buildSimulationApiRoutes(tmpDir);
    const result = routes.getSimulation("test_sim");
    expect(result).not.toBeNull();
    expect(result!.name).toBe("test_sim");
    expect(result!.summary.score).toBe(0.85);
  });

  it("getSimulation returns null for missing", () => {
    const routes = buildSimulationApiRoutes(tmpDir);
    expect(routes.getSimulation("nonexistent")).toBeNull();
  });

  it("getSimulation rejects escaping paths", () => {
    const hiddenDir = join(tmpDir, "secret");
    mkdirSync(hiddenDir, { recursive: true });
    writeFileSync(
      join(hiddenDir, "report.json"),
      JSON.stringify({ name: "secret", summary: { score: 1 } }, null, 2),
    );
    const routes = buildSimulationApiRoutes(tmpDir);
    expect(routes.getSimulation("../secret")).toBeNull();
  });

  it("getDashboardData returns visualization-ready structure", () => {
    writeSimReport("sweep_sim", {
      id: "sim_001",
      name: "sweep_sim",
      family: "simulation",
      status: "completed",
      summary: {
        score: 0.75,
        reasoning: "Mixed",
        dimensionScores: { reliability: 0.8, cost: 0.6 },
        bestCase: { score: 0.95, variables: { timeout: 30 } },
        worstCase: { score: 0.3, variables: { timeout: 5 } },
        mostSensitiveVariables: ["timeout", "retries"],
      },
      sweep: {
        dimensions: [{ variable: "timeout", values: [5, 15, 30] }],
        runs: 3,
        results: [
          {
            variables: { timeout: 5 },
            score: 0.3,
            reasoning: "Too fast",
            dimensionScores: {},
          },
          {
            variables: { timeout: 15 },
            score: 0.75,
            reasoning: "Ok",
            dimensionScores: {},
          },
          {
            variables: { timeout: 30 },
            score: 0.95,
            reasoning: "Best",
            dimensionScores: {},
          },
        ],
      },
      assumptions: ["stable network"],
      warnings: ["timeout sensitive"],
    });

    const routes = buildSimulationApiRoutes(tmpDir);
    const data = routes.getDashboardData("sweep_sim");
    expect(data).not.toBeNull();
    expect(data!.name).toBe("sweep_sim");
    expect(data!.overallScore).toBe(0.75);
    expect(data!.sensitivityRanking).toEqual(["timeout", "retries"]);
    expect(data!.sweepChart).toBeDefined();
    expect(data!.sweepChart!.length).toBe(3);
    expect(data!.dimensionScores).toEqual({ reliability: 0.8, cost: 0.6 });
  });

  it("getDashboardData handles simulation without sweep", () => {
    writeSimReport("simple_sim", {
      id: "sim_001",
      name: "simple_sim",
      family: "simulation",
      status: "completed",
      summary: {
        score: 0.9,
        reasoning: "Great",
        dimensionScores: { quality: 0.9 },
      },
      assumptions: [],
      warnings: [],
    });

    const routes = buildSimulationApiRoutes(tmpDir);
    const data = routes.getDashboardData("simple_sim");
    expect(data).not.toBeNull();
    expect(data!.overallScore).toBe(0.9);
    expect(data!.sweepChart).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// Dashboard HTML generation
// ---------------------------------------------------------------------------

describe("Dashboard HTML", () => {
  it("renderDashboardHtml returns valid HTML with chart containers", async () => {
    const { renderDashboardHtml } =
      await import("../src/server/simulation-dashboard.js");
    const html = renderDashboardHtml();
    expect(html).toContain("<!DOCTYPE html>");
    expect(html).toContain("simulation-dashboard");
    expect(html).toContain("sweep-chart");
    expect(html).toContain("sensitivity-chart");
  });
});

async function createSimulationDashboardServer(dir: string) {
  const { RunManager, InteractiveServer } = await import("../src/server/index.js");
  const { SQLiteStore } = await import("../src/storage/index.js");

  const dbPath = join(dir, "test.db");
  const runsRoot = join(dir, "runs");
  const knowledgeRoot = join(dir, "knowledge");
  mkdirSync(runsRoot, { recursive: true });
  mkdirSync(knowledgeRoot, { recursive: true });

  const store = new SQLiteStore(dbPath);
  store.migrate(join(__dirname, "..", "migrations"));
  store.close();

  writeSimReport("live_sim", {
    id: "sim_001",
    name: "live_sim",
    family: "simulation",
    status: "completed",
    summary: {
      score: 0.82,
      reasoning: "Stable run",
      dimensionScores: { quality: 0.82 },
      mostSensitiveVariables: ["timeout"],
    },
    assumptions: [],
    warnings: [],
  }, knowledgeRoot);

  const mgr = new RunManager({
    dbPath,
    migrationsDir: join(__dirname, "..", "migrations"),
    runsRoot,
    knowledgeRoot,
    providerType: "deterministic",
  });
  const server = new InteractiveServer({ runManager: mgr, port: 0 });
  await server.start();
  return { server, baseUrl: `http://localhost:${server.port}` };
}

describe("Simulation dashboard integration", () => {
  let server: Awaited<ReturnType<typeof createSimulationDashboardServer>>["server"];
  let baseUrl: string;

  beforeEach(async () => {
    const setup = await createSimulationDashboardServer(tmpDir);
    server = setup.server;
    baseUrl = setup.baseUrl;
  });

  afterEach(async () => {
    await server.stop();
  });

  it("mounts simulation REST endpoints on the live server", async () => {
    const listRes = await fetch(`${baseUrl}/api/simulations`);
    expect(listRes.status).toBe(200);
    const list = await listRes.json();
    expect(Array.isArray(list)).toBe(true);
    expect(list[0]?.name).toBe("live_sim");

    const detailRes = await fetch(`${baseUrl}/api/simulations/live_sim`);
    expect(detailRes.status).toBe(200);
    const detail = await detailRes.json();
    expect(detail.name).toBe("live_sim");

    const dashRes = await fetch(`${baseUrl}/api/simulations/live_sim/dashboard`);
    expect(dashRes.status).toBe(200);
    const dashboard = await dashRes.json();
    expect(dashboard.overallScore).toBe(0.82);
    expect(dashboard.sensitivityRanking).toEqual(["timeout"]);
  });

  it("serves the simulation dashboard HTML on /dashboard", async () => {
    const res = await fetch(`${baseUrl}/dashboard`);
    expect(res.status).toBe(200);
    const body = await res.text();
    expect(body).toContain("<!DOCTYPE html>");
    expect(body).toContain("simulation-dashboard");
  });

  it("returns 404 for escaped simulation names on the live server", async () => {
    const res = await fetch(`${baseUrl}/api/simulations/..%2Fsecret/dashboard`);
    expect(res.status).toBe(404);
  });
});
