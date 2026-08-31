import type { ServerResponse } from "node:http";

import { describe, expect, it, vi } from "vitest";

import type {
  RunSimulationApi,
  RunSimulationReadDeps,
  RunSimulationReadDepsWithPlaybook,
  RunSimulationReadRunManager,
} from "../src/server/run-simulation-read-workflow.js";
import type { HttpRouteContext } from "../src/server/routes/http-route-context.js";
import { tryRunListRoutes } from "../src/server/routes/run-list-routes.js";
import { tryScenarioSimulationRoutes } from "../src/server/routes/scenario-simulation-routes.js";

function routeContext(url: string, method: string): {
  ctx: HttpRouteContext;
  json: ReturnType<typeof vi.fn>;
  readJsonBody: ReturnType<typeof vi.fn>;
  setHeader: ReturnType<typeof vi.fn>;
} {
  const json = vi.fn();
  const readJsonBody = vi.fn(async () => ({ attack: true }));
  const setHeader = vi.fn();
  return {
    ctx: {
      url,
      method,
      requestUrl: new URL(url, "http://127.0.0.1"),
      res: { setHeader } as unknown as ServerResponse,
      json,
      readJsonBody,
    },
    json,
    readJsonBody,
    setHeader,
  };
}

function routeDependencies() {
  const store = {
    listRuns: vi.fn(() => [{ secret: "run-content" }]),
    getRun: vi.fn(() => ({ secret: "run-metadata" })),
    getGenerations: vi.fn(() => [{ secret: "generation-content" }]),
    close: vi.fn(),
  };
  const openStore = vi.fn(() => store);
  const runManager: RunSimulationReadRunManager = {
    getRunsRoot: vi.fn(() => "/runs"),
    getKnowledgeRoot: vi.fn(() => "/knowledge"),
    getEnvironmentInfo: vi.fn(() => ({ scenarios: [{ secret: "scenario-content" }] })),
  };
  const simulationApi: RunSimulationApi = {
    listSimulations: vi.fn(() => [{ secret: "simulation-list-content" }]),
    getSimulation: vi.fn(() => ({ secret: "simulation-content" })),
    getDashboardData: vi.fn(() => ({ secret: "dashboard-content" })),
  };
  const loadReplayArtifactResponse = vi.fn(() => ({
    status: 200,
    body: { secret: "replay-content" },
  }));
  const readPlaybook = vi.fn(() => "playbook-content");
  const runSimDeps: RunSimulationReadDeps = {
    openStore,
    loadReplayArtifactResponse,
  };
  const playbookDeps: RunSimulationReadDepsWithPlaybook = {
    ...runSimDeps,
    readPlaybook,
  };
  return {
    store,
    openStore,
    runManager,
    simulationApi,
    loadReplayArtifactResponse,
    readPlaybook,
    runSimDeps,
    playbookDeps,
  };
}

describe("read-only server route method enforcement", () => {
  it("does not expose run, replay, status, or playbook content to POST", async () => {
    const dependencies = routeDependencies();
    const paths = [
      "/api/runs",
      "/api/runs/run-1/replay/2",
      "/api/runs/run-1/status",
      "/api/knowledge/playbook/grid_ctf",
    ];

    for (const path of paths) {
      const { ctx, json, readJsonBody, setHeader } = routeContext(path, "POST");

      expect(await tryRunListRoutes(ctx, dependencies)).toBe(true);
      expect(setHeader).toHaveBeenCalledWith("Allow", "GET, HEAD");
      expect(json).toHaveBeenCalledOnce();
      expect(json).toHaveBeenCalledWith(405, { error: "Method not allowed" });
      expect(readJsonBody).not.toHaveBeenCalled();
    }

    expect(dependencies.openStore).not.toHaveBeenCalled();
    expect(dependencies.loadReplayArtifactResponse).not.toHaveBeenCalled();
    expect(dependencies.readPlaybook).not.toHaveBeenCalled();
    expect(dependencies.runManager.getRunsRoot).not.toHaveBeenCalled();
    expect(dependencies.runManager.getKnowledgeRoot).not.toHaveBeenCalled();
  });

  it("does not expose scenario or simulation content to POST", async () => {
    const dependencies = routeDependencies();
    const paths = [
      "/api/scenarios",
      "/api/simulations",
      "/api/simulations/sim-1",
      "/api/simulations/sim-1/dashboard",
    ];

    for (const path of paths) {
      const { ctx, json, readJsonBody, setHeader } = routeContext(path, "POST");

      expect(await tryScenarioSimulationRoutes(ctx, dependencies)).toBe(true);
      expect(setHeader).toHaveBeenCalledWith("Allow", "GET, HEAD");
      expect(json).toHaveBeenCalledOnce();
      expect(json).toHaveBeenCalledWith(405, { error: "Method not allowed" });
      expect(readJsonBody).not.toHaveBeenCalled();
    }

    expect(dependencies.runManager.getEnvironmentInfo).not.toHaveBeenCalled();
    expect(dependencies.simulationApi.listSimulations).not.toHaveBeenCalled();
    expect(dependencies.simulationApi.getSimulation).not.toHaveBeenCalled();
    expect(dependencies.simulationApi.getDashboardData).not.toHaveBeenCalled();
  });

  it("continues to dispatch GET and HEAD reads", async () => {
    for (const method of ["GET", "HEAD"]) {
      const runDependencies = routeDependencies();
      const run = routeContext("/api/runs", method);
      expect(await tryRunListRoutes(run.ctx, runDependencies)).toBe(true);
      expect(run.json).toHaveBeenCalledWith(200, [{ secret: "run-content" }]);

      const scenarioDependencies = routeDependencies();
      const scenarios = routeContext("/api/scenarios", method);
      expect(await tryScenarioSimulationRoutes(scenarios.ctx, scenarioDependencies)).toBe(true);
      expect(scenarios.json).toHaveBeenCalledWith(200, [{ secret: "scenario-content" }]);
    }
  });
});
