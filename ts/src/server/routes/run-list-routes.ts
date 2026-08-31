/**
 * /api/runs and /api/knowledge/playbook routes (AC-852).
 *
 * Both share the executeRunSimulationReadRequest dispatcher and its
 * RunSimulationReadDeps. The playbook route is the one call site with a
 * real readPlaybook implementation; the run routes below dispatch route
 * literals that never reach the playbook case, so their shared runSimDeps
 * omits readPlaybook entirely (AC-862; it is optional on the deps type).
 * playbookDeps uses RunSimulationReadDepsWithPlaybook so the compiler
 * requires readPlaybook at this one call site (AC-868).
 */

import {
  executeRunSimulationReadRequest,
  type RunSimulationApi,
  type RunSimulationReadDeps,
  type RunSimulationReadDepsWithPlaybook,
  type RunSimulationReadRunManager,
} from "../run-simulation-read-workflow.js";
import { asRunId, asScenarioName } from "../../domain/ids.js";
import type { HttpRouteContext } from "./http-route-context.js";

export async function tryRunListRoutes(
  ctx: HttpRouteContext,
  opts: {
    runManager: RunSimulationReadRunManager;
    simulationApi: RunSimulationApi;
    runSimDeps: RunSimulationReadDeps;
    playbookDeps: RunSimulationReadDepsWithPlaybook;
  },
): Promise<boolean> {
  const { runManager, simulationApi, runSimDeps, playbookDeps } = opts;

  // GET /api/runs
  if (ctx.url === "/api/runs" || ctx.url.startsWith("/api/runs?")) {
    if (rejectNonReadMethod(ctx)) return true;
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
    if (rejectNonReadMethod(ctx)) return true;
    const [, rawRunId, genStr] = replayMatch;
    const runId = asRunId(rawRunId!);
    const response = executeRunSimulationReadRequest({
      route: "run_replay",
      runId,
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
    if (rejectNonReadMethod(ctx)) return true;
    const [, rawRunId] = statusMatch;
    const runId = asRunId(rawRunId!);
    const response = executeRunSimulationReadRequest({
      route: "run_status",
      runId,
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
    if (rejectNonReadMethod(ctx)) return true;
    const [, rawScenario] = playbookMatch;
    const scenario = asScenarioName(rawScenario!);
    const response = executeRunSimulationReadRequest({
      route: "playbook",
      scenario,
      runManager,
      simulationApi,
      deps: playbookDeps,
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
