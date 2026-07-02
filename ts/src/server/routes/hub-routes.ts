/**
 * /api/hub routes (AC-852).
 */

import type { HubApiRoutes } from "../hub-api.js";
import type { HttpRouteContext } from "./http-route-context.js";

export async function tryHubRoutes(ctx: HttpRouteContext, hubApi: HubApiRoutes): Promise<boolean> {
  // Research hub session routes
  if (
    ctx.method === "GET" &&
    (ctx.url === "/api/hub/sessions" || ctx.url === "/api/hub/sessions/")
  ) {
    const response = hubApi.listSessions();
    ctx.json(response.status, response.body);
    return true;
  }

  const hubSessionHeartbeatMatch = ctx.url.match(/^\/api\/hub\/sessions\/([^/]+)\/heartbeat$/);
  if (ctx.method === "POST" && hubSessionHeartbeatMatch) {
    const [, rawSessionId] = hubSessionHeartbeatMatch;
    const response = hubApi.heartbeatSession(
      decodeURIComponent(rawSessionId!),
      await ctx.readJsonBody(),
    );
    ctx.json(response.status, response.body);
    return true;
  }

  const hubSessionMatch = ctx.url.match(/^\/api\/hub\/sessions\/([^/]+)$/);
  if (hubSessionMatch) {
    const [, rawSessionId] = hubSessionMatch;
    const sessionId = decodeURIComponent(rawSessionId!);
    if (ctx.method === "GET") {
      const response = hubApi.getSession(sessionId);
      ctx.json(response.status, response.body);
      return true;
    }
    if (ctx.method === "PUT") {
      const response = hubApi.upsertSession(sessionId, await ctx.readJsonBody());
      ctx.json(response.status, response.body);
      return true;
    }
  }

  // Research hub package routes
  const hubPackageFromRunMatch = ctx.url.match(/^\/api\/hub\/packages\/from-run\/([^/]+)$/);
  if (ctx.method === "POST" && hubPackageFromRunMatch) {
    const [, rawRunId] = hubPackageFromRunMatch;
    const response = hubApi.promotePackageFromRun(
      decodeURIComponent(rawRunId!),
      await ctx.readJsonBody(),
    );
    ctx.json(response.status, response.body);
    return true;
  }

  if (
    ctx.method === "GET" &&
    (ctx.url === "/api/hub/packages" || ctx.url === "/api/hub/packages/")
  ) {
    const response = hubApi.listPackages();
    ctx.json(response.status, response.body);
    return true;
  }

  const hubPackageAdoptMatch = ctx.url.match(/^\/api\/hub\/packages\/([^/]+)\/adopt$/);
  if (ctx.method === "POST" && hubPackageAdoptMatch) {
    const [, rawPackageId] = hubPackageAdoptMatch;
    const response = hubApi.adoptPackage(
      decodeURIComponent(rawPackageId!),
      await ctx.readJsonBody(),
    );
    ctx.json(response.status, response.body);
    return true;
  }

  const hubPackageMatch = ctx.url.match(/^\/api\/hub\/packages\/([^/]+)$/);
  if (ctx.method === "GET" && hubPackageMatch) {
    const [, rawPackageId] = hubPackageMatch;
    const response = hubApi.getPackage(decodeURIComponent(rawPackageId!));
    ctx.json(response.status, response.body);
    return true;
  }

  // Research hub result and promotion routes
  const hubResultFromRunMatch = ctx.url.match(/^\/api\/hub\/results\/from-run\/([^/]+)$/);
  if (ctx.method === "POST" && hubResultFromRunMatch) {
    const [, rawRunId] = hubResultFromRunMatch;
    const response = hubApi.materializeResultFromRun(
      decodeURIComponent(rawRunId!),
      await ctx.readJsonBody(),
    );
    ctx.json(response.status, response.body);
    return true;
  }

  if (ctx.method === "GET" && (ctx.url === "/api/hub/results" || ctx.url === "/api/hub/results/")) {
    const response = hubApi.listResults();
    ctx.json(response.status, response.body);
    return true;
  }

  const hubResultMatch = ctx.url.match(/^\/api\/hub\/results\/([^/]+)$/);
  if (ctx.method === "GET" && hubResultMatch) {
    const [, rawResultId] = hubResultMatch;
    const response = hubApi.getResult(decodeURIComponent(rawResultId!));
    ctx.json(response.status, response.body);
    return true;
  }

  if (
    ctx.method === "POST" &&
    (ctx.url === "/api/hub/promotions" || ctx.url === "/api/hub/promotions/")
  ) {
    const response = hubApi.createPromotion(await ctx.readJsonBody());
    ctx.json(response.status, response.body);
    return true;
  }

  if (ctx.method === "GET" && (ctx.url === "/api/hub/feed" || ctx.url === "/api/hub/feed/")) {
    const response = hubApi.feed();
    ctx.json(response.status, response.body);
    return true;
  }

  return false;
}
