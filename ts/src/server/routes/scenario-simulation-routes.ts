/**
 * /api/scenarios and /api/simulations routes (AC-852).
 *
 * Shares the executeRunSimulationReadRequest dispatcher with run-list-routes;
 * all four call sites here use the null-returning readPlaybook stub.
 */

import {
  executeRunSimulationReadRequest,
  type RunSimulationApi,
  type RunSimulationReadDeps,
  type RunSimulationReadRunManager,
} from "../run-simulation-read-workflow.js";
import type { HttpRouteContext } from "./http-route-context.js";

export async function tryScenarioSimulationRoutes(
  ctx: HttpRouteContext,
  opts: {
    runManager: RunSimulationReadRunManager;
    simulationApi: RunSimulationApi;
    runSimDeps: RunSimulationReadDeps;
  },
): Promise<boolean> {
  const { runManager, simulationApi, runSimDeps } = opts;

  // GET /api/scenarios
  if (ctx.url === "/api/scenarios") {
    const response = executeRunSimulationReadRequest({
      route: "scenarios",
      runManager,
      simulationApi,
      deps: runSimDeps,
    });
    ctx.json(response.status, response.body);
    return true;
  }

  // GET /api/simulations
  if (ctx.method === "GET" && ctx.url === "/api/simulations") {
    const response = executeRunSimulationReadRequest({
      route: "simulations_list",
      runManager,
      simulationApi,
      deps: runSimDeps,
    });
    ctx.json(response.status, response.body);
    return true;
  }

  // GET /api/simulations/:name
  const simulationMatch = ctx.url.match(/^\/api\/simulations\/([^/]+)$/);
  if (ctx.method === "GET" && simulationMatch) {
    const [, rawName] = simulationMatch;
    const response = executeRunSimulationReadRequest({
      route: "simulation_detail",
      simulationName: decodeURIComponent(rawName!),
      rawSimulationName: rawName!,
      runManager,
      simulationApi,
      deps: runSimDeps,
    });
    ctx.json(response.status, response.body);
    return true;
  }

  // GET /api/simulations/:name/dashboard
  const simulationDashboardMatch = ctx.url.match(/^\/api\/simulations\/([^/]+)\/dashboard$/);
  if (ctx.method === "GET" && simulationDashboardMatch) {
    const [, rawName] = simulationDashboardMatch;
    const response = executeRunSimulationReadRequest({
      route: "simulation_dashboard",
      simulationName: decodeURIComponent(rawName!),
      rawSimulationName: rawName!,
      runManager,
      simulationApi,
      deps: runSimDeps,
    });
    ctx.json(response.status, response.body);
    return true;
  }

  return false;
}
