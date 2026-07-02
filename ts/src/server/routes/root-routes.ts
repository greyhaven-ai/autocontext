/**
 * Root/info/dashboard/health/capabilities routes (AC-852).
 */

import { buildHttpApiParityMatrix } from "../http-api-parity.js";
import { renderDashboardHtml } from "../simulation-dashboard.js";
import type { HttpRouteContext } from "./http-route-context.js";

export function tryRootRoutes(ctx: HttpRouteContext): boolean {
  // Root endpoint — API info.
  if (ctx.url === "/") {
    ctx.json(200, {
      service: "autocontext",
      version: "0.2.4",
      endpoints: {
        health: "/health",
        dashboard: "/dashboard",
        capabilities: {
          http: "/api/capabilities/http",
        },
        runs: "/api/runs",
        simulations: "/api/simulations",
        scenarios: "/api/scenarios",
        knowledge: {
          scenarios: "/api/knowledge/scenarios",
          export: "/api/knowledge/export/:scenario",
          import: "/api/knowledge/import",
          search: "/api/knowledge/search",
          solve: "/api/knowledge/solve",
          playbook: "/api/knowledge/playbook/:scenario",
          lifecycle: "/api/knowledge/:scenario/lifecycle",
        },
        campaigns: "/api/campaigns",
        missions: "/api/missions",
        monitors: "/api/monitors",
        notebooks: "/api/notebooks",
        openclaw: "/api/openclaw",
        cockpit: "/api/cockpit",
        context_selection: "/api/cockpit/runs/:run_id/context-selection",
        trace_gates: "/api/cockpit/runs/:run_id/trace-gates",
        background_sessions: {
          list: "/api/cockpit/background-sessions",
          show: "/api/cockpit/background-sessions/:session_id",
        },
        runtime_sessions: {
          list: "/api/cockpit/runtime-sessions",
          show: "/api/cockpit/runtime-sessions/:session_id",
          timeline: "/api/cockpit/runtime-sessions/:session_id/timeline",
          run: "/api/cockpit/runs/:run_id/runtime-session",
          run_timeline: "/api/cockpit/runs/:run_id/runtime-session/timeline",
        },
        hub: "/api/hub",
        websocket: "/ws/interactive",
        events: "/ws/events",
      },
    });
    return true;
  }

  // Simulation dashboard HTML
  if (ctx.url === "/dashboard" || ctx.url === "/dashboard/") {
    ctx.res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    ctx.res.end(renderDashboardHtml());
    return true;
  }

  // Health
  if (ctx.url === "/health") {
    ctx.json(200, { status: "ok" });
    return true;
  }

  // GET /api/capabilities/http
  if (ctx.method === "GET" && ctx.url === "/api/capabilities/http") {
    ctx.json(200, buildHttpApiParityMatrix());
    return true;
  }

  return false;
}
