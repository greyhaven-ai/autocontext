import {
  readSystemMapReplay,
  readSystemMapView,
  SYSTEM_MAP_REPLAY_LIMIT,
  SYSTEM_MAP_TOPOLOGIES,
} from "../system-map.js";
import type { HttpRouteContext } from "./http-route-context.js";

export async function trySystemMapRoutes(
  ctx: HttpRouteContext,
  opts: { eventsPath: string },
): Promise<boolean> {
  const isExecutionView = ctx.url === "/system-map" || ctx.url === "/system-map/";
  const isContextView = ctx.url === "/system-map/context" || ctx.url === "/system-map/context/";
  const isActivationView = ctx.url === "/system-map/activation" || ctx.url === "/system-map/activation/";
  const isRoutingView = ctx.url === "/system-map/routing" || ctx.url === "/system-map/routing/";
  if (ctx.method === "GET" && (isExecutionView || isContextView || isActivationView || isRoutingView)) {
    const view = isContextView
      ? "context"
      : isActivationView
        ? "activation"
        : isRoutingView
          ? "routing"
          : readSystemMapView(ctx.requestUrl.searchParams.get("view"));
    const { renderSystemMapHtml } = await import("../system-map-page.js");
    ctx.res.writeHead(200, {
      "Content-Type": "text/html; charset=utf-8",
      "Content-Security-Policy": "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self' ws: wss:; object-src 'none'; base-uri 'none'",
      "Referrer-Policy": "no-referrer",
      "X-Content-Type-Options": "nosniff",
    });
    ctx.res.end(renderSystemMapHtml(SYSTEM_MAP_TOPOLOGIES[view]));
    return true;
  }

  if (ctx.method === "GET" && ctx.url === "/api/cockpit/system-map/topology") {
    const view = readSystemMapView(ctx.requestUrl.searchParams.get("view"));
    ctx.json(200, SYSTEM_MAP_TOPOLOGIES[view]);
    return true;
  }

  if (ctx.method === "GET" && ctx.url === "/api/cockpit/system-map/replay") {
    const limit = readPositiveInteger(
      ctx.requestUrl.searchParams.get("limit"),
      SYSTEM_MAP_REPLAY_LIMIT,
    );
    const runId = (ctx.requestUrl.searchParams.get("run_id") ?? "").trim();
    const view = readSystemMapView(ctx.requestUrl.searchParams.get("view"));
    const replay = readSystemMapReplay(opts.eventsPath, limit, view);
    ctx.json(200, {
      version: 1,
      view,
      run_id: runId,
      transfers: runId ? replay.filter((transfer) => transfer.runId === runId) : replay,
    });
    return true;
  }

  return false;
}

function readPositiveInteger(value: string | null, fallback: number): number {
  if (!value) return fallback;
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback;
}
