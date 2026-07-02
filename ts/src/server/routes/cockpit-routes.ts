/**
 * /api/cockpit routes (AC-852).
 */

import type { BackgroundSessionApiRoutes } from "../background-session-api.js";
import type { CockpitApiRoutes } from "../cockpit-api.js";
import type { RuntimeSessionApiRoutes } from "../runtime-session-api.js";
import type { TraceGateReviewApiRoutes } from "../trace-gate-review-api.js";
import type { HttpRouteContext } from "./http-route-context.js";

export async function tryCockpitRoutes(
  ctx: HttpRouteContext,
  apis: {
    cockpitApi: CockpitApiRoutes;
    backgroundSessionApi: BackgroundSessionApiRoutes;
    runtimeSessionApi: RuntimeSessionApiRoutes;
    traceGateReviewApi: TraceGateReviewApiRoutes;
  },
): Promise<boolean> {
  const { cockpitApi, backgroundSessionApi, runtimeSessionApi, traceGateReviewApi } = apis;

  // Cockpit notebook context routes
  if (
    ctx.method === "GET" &&
    (ctx.url === "/api/cockpit/notebooks" || ctx.url === "/api/cockpit/notebooks/")
  ) {
    const response = cockpitApi.listNotebooks();
    ctx.json(response.status, response.body);
    return true;
  }

  const cockpitNotebookEffectiveMatch = ctx.url.match(
    /^\/api\/cockpit\/notebooks\/([^/]+)\/effective-context$/,
  );
  if (ctx.method === "GET" && cockpitNotebookEffectiveMatch) {
    const [, rawSessionId] = cockpitNotebookEffectiveMatch;
    const response = cockpitApi.effectiveNotebookContext(decodeURIComponent(rawSessionId!));
    ctx.json(response.status, response.body);
    return true;
  }

  const cockpitNotebookMatch = ctx.url.match(/^\/api\/cockpit\/notebooks\/([^/]+)$/);
  if (cockpitNotebookMatch) {
    const [, rawSessionId] = cockpitNotebookMatch;
    const sessionId = decodeURIComponent(rawSessionId!);
    if (ctx.method === "GET") {
      const response = cockpitApi.getNotebook(sessionId);
      ctx.json(response.status, response.body);
      return true;
    }
    if (ctx.method === "PUT") {
      const response = cockpitApi.upsertNotebook(sessionId, await ctx.readJsonBody());
      ctx.json(response.status, response.body);
      return true;
    }
    if (ctx.method === "DELETE") {
      const response = cockpitApi.deleteNotebook(sessionId);
      ctx.json(response.status, response.body);
      return true;
    }
  }

  // Cockpit run routes
  if (
    ctx.method === "GET" &&
    (ctx.url === "/api/cockpit/runs" || ctx.url === "/api/cockpit/runs/")
  ) {
    const response = cockpitApi.listRuns();
    ctx.json(response.status, response.body);
    return true;
  }

  // Cockpit background-session routes
  if (
    ctx.method === "GET" &&
    (ctx.url === "/api/cockpit/background-sessions" ||
      ctx.url === "/api/cockpit/background-sessions/")
  ) {
    const response = backgroundSessionApi.list(ctx.requestUrl.searchParams);
    ctx.json(response.status, response.body);
    return true;
  }

  const cockpitBackgroundSessionMatch = ctx.url.match(
    /^\/api\/cockpit\/background-sessions\/([^/]+)$/,
  );
  if (ctx.method === "GET" && cockpitBackgroundSessionMatch) {
    const [, rawSessionId] = cockpitBackgroundSessionMatch;
    const response = backgroundSessionApi.getBySessionId(decodeURIComponent(rawSessionId!));
    ctx.json(response.status, response.body);
    return true;
  }

  // Cockpit runtime-session routes
  if (
    ctx.method === "GET" &&
    (ctx.url === "/api/cockpit/runtime-sessions" || ctx.url === "/api/cockpit/runtime-sessions/")
  ) {
    const response = runtimeSessionApi.list(ctx.requestUrl.searchParams);
    ctx.json(response.status, response.body);
    return true;
  }

  const cockpitRuntimeSessionTimelineMatch = ctx.url.match(
    /^\/api\/cockpit\/runtime-sessions\/([^/]+)\/timeline$/,
  );
  if (ctx.method === "GET" && cockpitRuntimeSessionTimelineMatch) {
    const [, rawSessionId] = cockpitRuntimeSessionTimelineMatch;
    const response = runtimeSessionApi.getTimelineBySessionId(decodeURIComponent(rawSessionId!));
    ctx.json(response.status, response.body);
    return true;
  }

  const cockpitRuntimeSessionMatch = ctx.url.match(/^\/api\/cockpit\/runtime-sessions\/([^/]+)$/);
  if (ctx.method === "GET" && cockpitRuntimeSessionMatch) {
    const [, rawSessionId] = cockpitRuntimeSessionMatch;
    const response = runtimeSessionApi.getBySessionId(decodeURIComponent(rawSessionId!));
    ctx.json(response.status, response.body);
    return true;
  }

  const cockpitRunRuntimeSessionTimelineMatch = ctx.url.match(
    /^\/api\/cockpit\/runs\/([^/]+)\/runtime-session\/timeline$/,
  );
  if (ctx.method === "GET" && cockpitRunRuntimeSessionTimelineMatch) {
    const [, rawRunId] = cockpitRunRuntimeSessionTimelineMatch;
    const response = runtimeSessionApi.getTimelineByRunId(decodeURIComponent(rawRunId!));
    ctx.json(response.status, response.body);
    return true;
  }

  const cockpitRunRuntimeSessionMatch = ctx.url.match(
    /^\/api\/cockpit\/runs\/([^/]+)\/runtime-session$/,
  );
  if (ctx.method === "GET" && cockpitRunRuntimeSessionMatch) {
    const [, rawRunId] = cockpitRunRuntimeSessionMatch;
    const response = runtimeSessionApi.getByRunId(decodeURIComponent(rawRunId!));
    ctx.json(response.status, response.body);
    return true;
  }

  const cockpitTraceGateReviewMatch = ctx.url.match(/^\/api\/cockpit\/runs\/([^/]+)\/trace-gates$/);
  if (ctx.method === "GET" && cockpitTraceGateReviewMatch) {
    const [, rawRunId] = cockpitTraceGateReviewMatch;
    const response = traceGateReviewApi.getByRunId(decodeURIComponent(rawRunId!));
    ctx.json(response.status, response.body);
    return true;
  }

  const cockpitContextSelectionMatch = ctx.url.match(
    /^\/api\/cockpit\/runs\/([^/]+)\/context-selection$/,
  );
  if (ctx.method === "GET" && cockpitContextSelectionMatch) {
    const [, rawRunId] = cockpitContextSelectionMatch;
    const response = cockpitApi.contextSelection(decodeURIComponent(rawRunId!));
    ctx.json(response.status, response.body);
    return true;
  }

  const cockpitCompareMatch = ctx.url.match(
    /^\/api\/cockpit\/runs\/([^/]+)\/compare\/(\d+)\/(\d+)$/,
  );
  if (ctx.method === "GET" && cockpitCompareMatch) {
    const [, rawRunId, rawGenA, rawGenB] = cockpitCompareMatch;
    const response = cockpitApi.compareGenerations(
      decodeURIComponent(rawRunId!),
      Number.parseInt(rawGenA!, 10),
      Number.parseInt(rawGenB!, 10),
    );
    ctx.json(response.status, response.body);
    return true;
  }

  const cockpitRunResourceMatch = ctx.url.match(
    /^\/api\/cockpit\/runs\/([^/]+)\/(status|changelog|resume|consultations)$/,
  );
  if (ctx.method === "GET" && cockpitRunResourceMatch) {
    const [, rawRunId, resource] = cockpitRunResourceMatch;
    const runId = decodeURIComponent(rawRunId!);
    const response =
      resource === "status"
        ? cockpitApi.runStatus(runId)
        : resource === "changelog"
          ? cockpitApi.changelog(runId)
          : resource === "resume"
            ? cockpitApi.resumeInfo(runId)
            : cockpitApi.listConsultations(runId);
    ctx.json(response.status, response.body);
    return true;
  }

  const cockpitConsultMatch = ctx.url.match(/^\/api\/cockpit\/runs\/([^/]+)\/consult$/);
  if (ctx.method === "POST" && cockpitConsultMatch) {
    const [, rawRunId] = cockpitConsultMatch;
    const response = await cockpitApi.requestConsultation(
      decodeURIComponent(rawRunId!),
      await ctx.readJsonBody(),
    );
    ctx.json(response.status, response.body);
    return true;
  }

  const cockpitWriteupMatch = ctx.url.match(/^\/api\/cockpit\/writeup\/([^/]+)$/);
  if (ctx.method === "GET" && cockpitWriteupMatch) {
    const [, rawRunId] = cockpitWriteupMatch;
    const response = cockpitApi.writeup(decodeURIComponent(rawRunId!));
    ctx.json(response.status, response.body);
    return true;
  }

  return false;
}
