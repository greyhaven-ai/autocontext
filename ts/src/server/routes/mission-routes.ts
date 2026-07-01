/**
 * /api/missions routes (AC-852).
 */

import type { MissionApiRoutes } from "../mission-api.js";
import {
  executeMissionActionRequest,
  type MissionActionRunManager,
  type MissionActionManager,
} from "../mission-action-workflow.js";
import { executeMissionReadRequest } from "../mission-read-workflow.js";
import type { HttpRouteContext } from "./http-route-context.js";

export async function tryMissionRoutes(
  ctx: HttpRouteContext,
  opts: {
    missionApi: MissionApiRoutes;
    missionManager: MissionActionManager;
    runManager: MissionActionRunManager;
  },
): Promise<boolean> {
  const { missionApi, missionManager, runManager } = opts;

  // GET /api/missions
  if (ctx.method === "GET" && ctx.url === "/api/missions") {
    ctx.json(200, missionApi.listMissions(ctx.requestUrl.searchParams.get("status") ?? undefined));
    return true;
  }

  // GET /api/missions/:id
  const missionMatch = ctx.url.match(/^\/api\/missions\/([^/]+)$/);
  if (ctx.method === "GET" && missionMatch) {
    const [, missionId] = missionMatch;
    const response = executeMissionReadRequest({
      missionId: missionId!,
      resource: "detail",
      missionManager,
      missionApi,
    });
    ctx.json(response.status, response.body);
    return true;
  }

  // GET /api/missions/:id/steps
  const missionStepsMatch = ctx.url.match(/^\/api\/missions\/([^/]+)\/steps$/);
  if (ctx.method === "GET" && missionStepsMatch) {
    const [, missionId] = missionStepsMatch;
    const response = executeMissionReadRequest({
      missionId: missionId!,
      resource: "steps",
      missionManager,
      missionApi,
    });
    ctx.json(response.status, response.body);
    return true;
  }

  // GET /api/missions/:id/subgoals
  const missionSubgoalsMatch = ctx.url.match(/^\/api\/missions\/([^/]+)\/subgoals$/);
  if (ctx.method === "GET" && missionSubgoalsMatch) {
    const [, missionId] = missionSubgoalsMatch;
    const response = executeMissionReadRequest({
      missionId: missionId!,
      resource: "subgoals",
      missionManager,
      missionApi,
    });
    ctx.json(response.status, response.body);
    return true;
  }

  // GET /api/missions/:id/budget
  const missionBudgetMatch = ctx.url.match(/^\/api\/missions\/([^/]+)\/budget$/);
  if (ctx.method === "GET" && missionBudgetMatch) {
    const [, missionId] = missionBudgetMatch;
    const response = executeMissionReadRequest({
      missionId: missionId!,
      resource: "budget",
      missionManager,
      missionApi,
    });
    ctx.json(response.status, response.body);
    return true;
  }

  // GET /api/missions/:id/artifacts
  const missionArtifactsMatch = ctx.url.match(/^\/api\/missions\/([^/]+)\/artifacts$/);
  if (ctx.method === "GET" && missionArtifactsMatch) {
    const [, missionId] = missionArtifactsMatch;
    const response = executeMissionReadRequest({
      missionId: missionId!,
      resource: "artifacts",
      missionManager,
      missionApi,
    });
    ctx.json(response.status, response.body);
    return true;
  }

  // POST /api/missions/:id/(run|pause|resume|cancel)
  const missionActionMatch = ctx.url.match(/^\/api\/missions\/([^/]+)\/(run|pause|resume|cancel)$/);
  if (ctx.method === "POST" && missionActionMatch) {
    const [, missionId, action] = missionActionMatch;
    const body = action === "run" ? await ctx.readJsonBody() : {};
    const response = await executeMissionActionRequest({
      action: action as "run" | "pause" | "resume" | "cancel",
      missionId: missionId!,
      body,
      missionManager,
      runManager,
    });
    ctx.json(response.status, response.body);
    return true;
  }

  return false;
}
