/**
 * /api/monitors routes (AC-852).
 */

import type { MonitorApiRoutes } from "../monitor-api.js";
import type { HttpRouteContext } from "./http-route-context.js";

export async function tryMonitorRoutes(
  ctx: HttpRouteContext,
  monitorApi: MonitorApiRoutes,
): Promise<boolean> {
  // GET/POST /api/monitors
  if (ctx.url === "/api/monitors" || ctx.url === "/api/monitors/") {
    if (ctx.method === "GET") {
      const response = monitorApi.list(ctx.requestUrl.searchParams);
      ctx.json(response.status, response.body);
      return true;
    }
    if (ctx.method === "POST") {
      const response = monitorApi.create(await ctx.readJsonBody());
      ctx.json(response.status, response.body);
      return true;
    }
  }

  // GET /api/monitors/alerts
  if (ctx.method === "GET" && ctx.url === "/api/monitors/alerts") {
    const response = monitorApi.listAlerts(ctx.requestUrl.searchParams);
    ctx.json(response.status, response.body);
    return true;
  }

  // POST /api/monitors/:conditionId/wait
  const monitorWaitMatch = ctx.url.match(/^\/api\/monitors\/([^/]+)\/wait$/);
  if (ctx.method === "POST" && monitorWaitMatch) {
    const [, rawConditionId] = monitorWaitMatch;
    const response = await monitorApi.wait(
      decodeURIComponent(rawConditionId!),
      ctx.requestUrl.searchParams,
    );
    ctx.json(response.status, response.body);
    return true;
  }

  // DELETE /api/monitors/:conditionId
  const monitorMatch = ctx.url.match(/^\/api\/monitors\/([^/]+)$/);
  if (ctx.method === "DELETE" && monitorMatch) {
    const [, rawConditionId] = monitorMatch;
    const response = monitorApi.delete(decodeURIComponent(rawConditionId!));
    ctx.json(response.status, response.body);
    return true;
  }

  return false;
}
