/**
 * /api/knowledge routes (AC-852), except /api/knowledge/playbook/:scenario
 * which is dispatched via run-list-routes.ts (executeRunSimulationReadRequest).
 */

import type { KnowledgeApiRoutes } from "../knowledge-api.js";
import { asScenarioName } from "../../domain/ids.js";
import type { HttpRouteContext } from "./http-route-context.js";

export async function tryKnowledgeRoutes(
  ctx: HttpRouteContext,
  knowledgeApi: KnowledgeApiRoutes,
): Promise<boolean> {
  // GET /api/knowledge/scenarios
  if (ctx.method === "GET" && ctx.url === "/api/knowledge/scenarios") {
    const response = knowledgeApi.listSolved();
    ctx.json(response.status, response.body);
    return true;
  }

  // GET /api/knowledge/export/:scenario
  const knowledgeExportMatch = ctx.url.match(/^\/api\/knowledge\/export\/([^/]+)$/);
  if (ctx.method === "GET" && knowledgeExportMatch) {
    const [, rawScenario] = knowledgeExportMatch;
    const response = knowledgeApi.exportScenario(asScenarioName(decodeURIComponent(rawScenario!)));
    ctx.json(response.status, response.body);
    return true;
  }

  // POST /api/knowledge/import
  if (ctx.method === "POST" && ctx.url === "/api/knowledge/import") {
    const response = knowledgeApi.importPackage(await ctx.readJsonBody());
    ctx.json(response.status, response.body);
    return true;
  }

  // POST /api/knowledge/search
  if (ctx.method === "POST" && ctx.url === "/api/knowledge/search") {
    const response = knowledgeApi.search(await ctx.readJsonBody());
    ctx.json(response.status, response.body);
    return true;
  }

  // POST /api/knowledge/solve
  if (ctx.method === "POST" && ctx.url === "/api/knowledge/solve") {
    const response = knowledgeApi.submitSolve(await ctx.readJsonBody());
    ctx.json(response.status, response.body);
    return true;
  }

  // GET/POST /api/knowledge/:scenario/playbook/(pending|approve|reject)
  const pendingPlaybookMatch = ctx.url.match(/^\/api\/knowledge\/([^/]+)\/playbook\/pending$/);
  if (ctx.method === "GET" && pendingPlaybookMatch) {
    const [, rawScenario] = pendingPlaybookMatch;
    const response = knowledgeApi.pendingPlaybook(asScenarioName(decodeURIComponent(rawScenario!)));
    ctx.json(response.status, response.body);
    return true;
  }
  const approvePlaybookMatch = ctx.url.match(/^\/api\/knowledge\/([^/]+)\/playbook\/approve$/);
  if (ctx.method === "POST" && approvePlaybookMatch) {
    const [, rawScenario] = approvePlaybookMatch;
    const response = knowledgeApi.approvePendingPlaybook(
      asScenarioName(decodeURIComponent(rawScenario!)),
    );
    ctx.json(response.status, response.body);
    return true;
  }
  const rejectPlaybookMatch = ctx.url.match(/^\/api\/knowledge\/([^/]+)\/playbook\/reject$/);
  if (ctx.method === "POST" && rejectPlaybookMatch) {
    const [, rawScenario] = rejectPlaybookMatch;
    const response = knowledgeApi.rejectPendingPlaybook(
      asScenarioName(decodeURIComponent(rawScenario!)),
    );
    ctx.json(response.status, response.body);
    return true;
  }

  // GET /api/knowledge/:scenario/lifecycle
  const lessonLifecycleMatch = ctx.url.match(/^\/api\/knowledge\/([^/]+)\/lifecycle$/);
  if (ctx.method === "GET" && lessonLifecycleMatch) {
    const [, rawScenario] = lessonLifecycleMatch;
    const response = knowledgeApi.lessonLifecycle(asScenarioName(decodeURIComponent(rawScenario!)));
    ctx.json(response.status, response.body);
    return true;
  }

  // POST /api/knowledge/:scenario/lessons/:lessonId/(approve|reject|curate)
  const lessonActionMatch = ctx.url.match(
    /^\/api\/knowledge\/([^/]+)\/lessons\/([^/]+)\/(approve|reject|curate)$/,
  );
  if (ctx.method === "POST" && lessonActionMatch) {
    const [, rawScenario, rawLessonId, action] = lessonActionMatch;
    const scenario = asScenarioName(decodeURIComponent(rawScenario!));
    const lessonId = decodeURIComponent(rawLessonId!);
    const response =
      action === "approve"
        ? knowledgeApi.approveLesson(scenario, lessonId)
        : action === "reject"
          ? knowledgeApi.rejectLesson(scenario, lessonId)
          : knowledgeApi.curateLesson(scenario, lessonId, await ctx.readJsonBody());
    ctx.json(response.status, response.body);
    return true;
  }

  // GET /api/knowledge/solve/:jobId
  const knowledgeSolveMatch = ctx.url.match(/^\/api\/knowledge\/solve\/([^/]+)$/);
  if (ctx.method === "GET" && knowledgeSolveMatch) {
    const [, rawJobId] = knowledgeSolveMatch;
    const response = knowledgeApi.solveStatus(decodeURIComponent(rawJobId!));
    ctx.json(response.status, response.body);
    return true;
  }

  return false;
}
