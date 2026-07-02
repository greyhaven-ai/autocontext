/**
 * /api/openclaw routes (AC-852).
 */

import type { OpenClawApiRoutes } from "../openclaw-api.js";
import { asScenarioName } from "../../domain/ids.js";
import type { HttpRouteContext } from "./http-route-context.js";

export async function tryOpenClawRoutes(
  ctx: HttpRouteContext,
  openClawApi: OpenClawApiRoutes,
): Promise<boolean> {
  // POST /api/openclaw/evaluate
  if (ctx.method === "POST" && ctx.url === "/api/openclaw/evaluate") {
    const response = openClawApi.evaluate(await ctx.readJsonBody());
    ctx.json(response.status, response.body);
    return true;
  }

  // POST /api/openclaw/validate
  if (ctx.method === "POST" && ctx.url === "/api/openclaw/validate") {
    const response = openClawApi.validate(await ctx.readJsonBody());
    ctx.json(response.status, response.body);
    return true;
  }

  // GET/POST /api/openclaw/artifacts
  if (ctx.url === "/api/openclaw/artifacts" || ctx.url === "/api/openclaw/artifacts/") {
    if (ctx.method === "GET") {
      const response = openClawApi.listArtifacts(ctx.requestUrl.searchParams);
      ctx.json(response.status, response.body);
      return true;
    }
    if (ctx.method === "POST") {
      const response = openClawApi.publishArtifact(await ctx.readJsonBody());
      ctx.json(response.status, response.body);
      return true;
    }
  }

  // GET /api/openclaw/artifacts/:artifactId
  const openClawArtifactMatch = ctx.url.match(/^\/api\/openclaw\/artifacts\/([^/]+)$/);
  if (ctx.method === "GET" && openClawArtifactMatch) {
    const [, rawArtifactId] = openClawArtifactMatch;
    const response = openClawApi.fetchArtifact(decodeURIComponent(rawArtifactId!));
    ctx.json(response.status, response.body);
    return true;
  }

  // GET/POST /api/openclaw/distill
  if (ctx.url === "/api/openclaw/distill" || ctx.url === "/api/openclaw/distill/") {
    if (ctx.method === "GET") {
      const response = openClawApi.distillStatus(ctx.requestUrl.searchParams);
      ctx.json(response.status, response.body);
      return true;
    }
    if (ctx.method === "POST") {
      const response = openClawApi.triggerDistillation(await ctx.readJsonBody());
      ctx.json(response.status, response.body);
      return true;
    }
  }

  // GET/PATCH /api/openclaw/distill/:jobId
  const openClawDistillMatch = ctx.url.match(/^\/api\/openclaw\/distill\/([^/]+)$/);
  if (openClawDistillMatch) {
    const [, rawJobId] = openClawDistillMatch;
    const jobId = decodeURIComponent(rawJobId!);
    if (ctx.method === "GET") {
      const response = openClawApi.getDistillJob(jobId);
      ctx.json(response.status, response.body);
      return true;
    }
    if (ctx.method === "PATCH") {
      const response = openClawApi.updateDistillJob(jobId, await ctx.readJsonBody());
      ctx.json(response.status, response.body);
      return true;
    }
  }

  // GET /api/openclaw/capabilities
  if (ctx.method === "GET" && ctx.url === "/api/openclaw/capabilities") {
    const response = openClawApi.capabilities();
    ctx.json(response.status, response.body);
    return true;
  }

  // GET /api/openclaw/discovery/capabilities
  if (ctx.method === "GET" && ctx.url === "/api/openclaw/discovery/capabilities") {
    const response = openClawApi.discoveryCapabilities();
    ctx.json(response.status, response.body);
    return true;
  }

  // GET /api/openclaw/discovery/health
  if (ctx.method === "GET" && ctx.url === "/api/openclaw/discovery/health") {
    const response = openClawApi.discoveryHealth();
    ctx.json(response.status, response.body);
    return true;
  }

  // GET /api/openclaw/discovery/scenario/:scenarioName/artifacts
  const openClawScenarioArtifactsMatch = ctx.url.match(
    /^\/api\/openclaw\/discovery\/scenario\/([^/]+)\/artifacts$/,
  );
  if (ctx.method === "GET" && openClawScenarioArtifactsMatch) {
    const [, rawScenarioName] = openClawScenarioArtifactsMatch;
    const response = openClawApi.discoveryScenarioArtifacts(
      asScenarioName(decodeURIComponent(rawScenarioName!)),
    );
    ctx.json(response.status, response.body);
    return true;
  }

  // GET /api/openclaw/discovery/scenario/:scenarioName
  const openClawScenarioMatch = ctx.url.match(/^\/api\/openclaw\/discovery\/scenario\/([^/]+)$/);
  if (ctx.method === "GET" && openClawScenarioMatch) {
    const [, rawScenarioName] = openClawScenarioMatch;
    const response = openClawApi.discoveryScenario(
      asScenarioName(decodeURIComponent(rawScenarioName!)),
    );
    ctx.json(response.status, response.body);
    return true;
  }

  // GET /api/openclaw/skill/manifest
  if (ctx.method === "GET" && ctx.url === "/api/openclaw/skill/manifest") {
    const response = openClawApi.skillManifest();
    ctx.json(response.status, response.body);
    return true;
  }

  return false;
}
