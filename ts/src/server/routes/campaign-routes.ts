/**
 * /api/campaigns routes (AC-852).
 */

import type { CampaignApiRoutes } from "../campaign-api.js";
import { executeCampaignRouteRequest } from "../campaign-route-workflow.js";
import type { HttpRouteContext } from "./http-route-context.js";

export async function tryCampaignRoutes(
  ctx: HttpRouteContext,
  opts: {
    campaignApi: CampaignApiRoutes;
    campaignManager: { budgetUsage(campaignId: string): unknown };
  },
): Promise<boolean> {
  const { campaignApi, campaignManager } = opts;

  // GET /api/campaigns
  if (ctx.method === "GET" && ctx.url === "/api/campaigns") {
    const response = executeCampaignRouteRequest({
      route: "list",
      queryStatus: ctx.requestUrl.searchParams.get("status") ?? undefined,
      body: {},
      campaignApi,
      campaignManager,
    });
    ctx.json(response.status, response.body);
    return true;
  }

  // POST /api/campaigns
  if (ctx.method === "POST" && ctx.url === "/api/campaigns") {
    const response = executeCampaignRouteRequest({
      route: "create",
      body: await ctx.readJsonBody(),
      campaignApi,
      campaignManager,
    });
    ctx.json(response.status, response.body);
    return true;
  }

  // GET /api/campaigns/:id
  const campaignMatch = ctx.url.match(/^\/api\/campaigns\/([^/]+)$/);
  if (ctx.method === "GET" && campaignMatch) {
    const [, campaignId] = campaignMatch;
    const response = executeCampaignRouteRequest({
      route: "detail",
      campaignId: campaignId!,
      body: {},
      campaignApi,
      campaignManager,
    });
    ctx.json(response.status, response.body);
    return true;
  }

  // GET /api/campaigns/:id/progress
  const campaignProgressMatch = ctx.url.match(/^\/api\/campaigns\/([^/]+)\/progress$/);
  if (ctx.method === "GET" && campaignProgressMatch) {
    const [, campaignId] = campaignProgressMatch;
    const response = executeCampaignRouteRequest({
      route: "progress",
      campaignId: campaignId!,
      body: {},
      campaignApi,
      campaignManager,
    });
    ctx.json(response.status, response.body);
    return true;
  }

  // POST /api/campaigns/:id/missions
  const campaignMissionMatch = ctx.url.match(/^\/api\/campaigns\/([^/]+)\/missions$/);
  if (ctx.method === "POST" && campaignMissionMatch) {
    const [, campaignId] = campaignMissionMatch;
    const response = executeCampaignRouteRequest({
      route: "add_mission",
      campaignId: campaignId!,
      body: await ctx.readJsonBody(),
      campaignApi,
      campaignManager,
    });
    ctx.json(response.status, response.body);
    return true;
  }

  // POST /api/campaigns/:id/(pause|resume|cancel)
  const campaignActionMatch = ctx.url.match(/^\/api\/campaigns\/([^/]+)\/(pause|resume|cancel)$/);
  if (ctx.method === "POST" && campaignActionMatch) {
    const [, campaignId, action] = campaignActionMatch;
    const response = executeCampaignRouteRequest({
      route: "status",
      campaignId: campaignId!,
      action: action as "pause" | "resume" | "cancel",
      body: {},
      campaignApi,
      campaignManager,
    });
    ctx.json(response.status, response.body);
    return true;
  }

  return false;
}
