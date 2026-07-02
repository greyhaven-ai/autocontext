/**
 * /api/notebooks routes (AC-852).
 */

import type { NotebookApiRoutes } from "../notebook-api.js";
import type { HttpRouteContext } from "./http-route-context.js";

export async function tryNotebookRoutes(
  ctx: HttpRouteContext,
  notebookApi: NotebookApiRoutes,
): Promise<boolean> {
  // GET /api/notebooks
  if (ctx.method === "GET" && (ctx.url === "/api/notebooks" || ctx.url === "/api/notebooks/")) {
    const response = notebookApi.list();
    ctx.json(response.status, response.body);
    return true;
  }

  // GET/PUT/DELETE /api/notebooks/:sessionId
  const notebookMatch = ctx.url.match(/^\/api\/notebooks\/([^/]+)$/);
  if (notebookMatch) {
    const [, rawSessionId] = notebookMatch;
    const sessionId = decodeURIComponent(rawSessionId!);
    if (ctx.method === "GET") {
      const response = notebookApi.get(sessionId);
      ctx.json(response.status, response.body);
      return true;
    }
    if (ctx.method === "PUT") {
      const response = notebookApi.upsert(sessionId, await ctx.readJsonBody());
      ctx.json(response.status, response.body);
      return true;
    }
    if (ctx.method === "DELETE") {
      const response = notebookApi.delete(sessionId);
      ctx.json(response.status, response.body);
      return true;
    }
  }

  return false;
}
