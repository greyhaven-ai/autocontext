/**
 * /api/runs and /api/knowledge/playbook routes (AC-852).
 *
 * Both share the executeRunSimulationReadRequest dispatcher and its
 * RunSimulationReadDeps. The playbook route is the one call site with a
 * real readPlaybook implementation; the run routes below dispatch route
 * literals that never reach the playbook case, so their shared runSimDeps
 * omits readPlaybook entirely (AC-862; it is optional on the deps type).
 */

import {
  executeRunSimulationReadRequest,
  type RunSimulationApi,
  type RunSimulationReadDeps,
  type RunSimulationReadRunManager,
} from "../run-simulation-read-workflow.js";
import type { HttpRouteContext } from "./http-route-context.js";

export async function tryRunListRoutes(
  ctx: HttpRouteContext,
  opts: {
    runManager: RunSimulationReadRunManager;
    simulationApi: RunSimulationApi;
    runSimDeps: RunSimulationReadDeps;
    playbookDeps: RunSimulationReadDeps;
  },
): Promise<boolean> {
  const { runManager, simulationApi, runSimDeps, playbookDeps } = opts;

  // GET /api/runs
  if (ctx.url === "/api/runs" || ctx.url.startsWith("/api/runs?")) {
    const response = executeRunSimulationReadRequest({
      route: "runs_list",
      runManager,
      simulationApi,
      deps: runSimDeps,
    });
    ctx.json(response.status, response.body);
    return true;
  }

  // GET /api/runs/:id/replay/:gen
  const replayMatch = ctx.url.match(/^\/api\/runs\/([^/]+)\/replay\/(\d+)$/);
  if (replayMatch) {
    const [, runId, genStr] = replayMatch;
    const response = executeRunSimulationReadRequest({
      route: "run_replay",
      runId: runId!,
      generation: parseInt(genStr!, 10),
      runManager,
      simulationApi,
      deps: runSimDeps,
    });
    ctx.json(response.status, response.body);
    return true;
  }

  // GET /api/runs/:id/status
  const statusMatch = ctx.url.match(/^\/api\/runs\/([^/]+)\/status$/);
  if (statusMatch) {
    const [, runId] = statusMatch;
    const response = executeRunSimulationReadRequest({
      route: "run_status",
      runId: runId!,
      runManager,
      simulationApi,
      deps: runSimDeps,
    });
    ctx.json(response.status, response.body);
    return true;
  }

  // GET /api/knowledge/playbook/:scenario
  const playbookMatch = ctx.url.match(/^\/api\/knowledge\/playbook\/([^/]+)$/);
  if (playbookMatch) {
    const [, scenario] = playbookMatch;
    const response = executeRunSimulationReadRequest({
      route: "playbook",
      scenario: scenario!,
      runManager,
      simulationApi,
      deps: playbookDeps,
    });
    ctx.json(response.status, response.body);
    return true;
  }

  return false;
}
