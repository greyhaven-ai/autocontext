/**
 * /api/scenarios and /api/simulations routes (AC-852).
 *
 * Shares the executeRunSimulationReadRequest dispatcher with run-list-routes.
 * None of the four routes here dispatch the "playbook" case, so their
 * shared runSimDeps omits readPlaybook (AC-862; optional on the deps type).
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
    if (rejectNonReadMethod(ctx)) return true;
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
  if (ctx.url === "/api/simulations") {
    if (rejectNonReadMethod(ctx)) return true;
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
  if (simulationMatch) {
    if (rejectNonReadMethod(ctx)) return true;
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
  if (simulationDashboardMatch) {
    if (rejectNonReadMethod(ctx)) return true;
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

function rejectNonReadMethod(ctx: HttpRouteContext): boolean {
  if (ctx.method === "GET" || ctx.method === "HEAD") return false;
  ctx.res.setHeader("Allow", "GET, HEAD");
  ctx.json(405, { error: "Method not allowed" });
  return true;
}
