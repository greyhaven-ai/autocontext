export type HttpApiRuntime = "python" | "typescript";
export type HttpApiMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE" | "WEBSOCKET";
export type HttpApiSupport = "supported" | "unsupported";
export type HttpApiParityStatus = "aligned" | "typescript_gap" | "python_gap";

export interface RuntimeRouteSupport {
  support: HttpApiSupport;
  source?: string;
}

export interface HttpApiParityEntry {
  method: HttpApiMethod;
  path: string;
  domain: string;
  python: RuntimeRouteSupport;
  typescript: RuntimeRouteSupport;
  status: HttpApiParityStatus;
  issue?: string;
  notes?: string;
}

export interface HttpApiParityMatrix {
  version: 1;
  runtimes: HttpApiRuntime[];
  summary: Record<HttpApiParityStatus, number>;
  routes: HttpApiParityEntry[];
}

const PY_APP = "autocontext/src/autocontext/server/app.py";
const TS_SERVER = "ts/src/server/ws-server.ts";

function both(
  domain: string,
  method: HttpApiMethod,
  path: string,
  pythonSource = PY_APP,
  typescriptSource = TS_SERVER,
  notes?: string,
): HttpApiParityEntry {
  return {
    domain,
    method,
    path,
    python: { support: "supported", source: pythonSource },
    typescript: { support: "supported", source: typescriptSource },
    status: "aligned",
    ...(notes ? { notes } : {}),
  };
}

function pythonOnly(
  domain: string,
  method: HttpApiMethod,
  path: string,
  source: string,
  notes: string,
): HttpApiParityEntry {
  return {
    domain,
    method,
    path,
    python: { support: "supported", source },
    typescript: { support: "unsupported" },
    status: "typescript_gap",
    issue: "AC-627",
    notes,
  };
}

function typescriptOnly(
  domain: string,
  method: HttpApiMethod,
  path: string,
  notes: string,
): HttpApiParityEntry {
  return {
    domain,
    method,
    path,
    python: { support: "unsupported" },
    typescript: { support: "supported", source: TS_SERVER },
    status: "python_gap",
    notes,
  };
}

export const HTTP_API_PARITY_ROUTES: readonly HttpApiParityEntry[] = [
  both("core", "GET", "/health"),
  both("core", "GET", "/api/runs"),
  both("core", "GET", "/api/runs/:run_id/status"),
  both("core", "GET", "/api/runs/:run_id/replay/:generation"),
  both("core", "WEBSOCKET", "/ws/events"),
  both("core", "WEBSOCKET", "/ws/interactive"),

  both("knowledge", "GET", "/api/knowledge/scenarios", "autocontext/src/autocontext/server/knowledge_api.py"),
  both("knowledge", "GET", "/api/knowledge/export/:scenario", "autocontext/src/autocontext/server/knowledge_api.py"),
  both("knowledge", "POST", "/api/knowledge/import", "autocontext/src/autocontext/server/knowledge_api.py"),
  both("knowledge", "POST", "/api/knowledge/search", "autocontext/src/autocontext/server/knowledge_api.py"),
  both("knowledge", "POST", "/api/knowledge/solve", "autocontext/src/autocontext/server/knowledge_api.py"),
  both("knowledge", "GET", "/api/knowledge/solve/:job_id", "autocontext/src/autocontext/server/knowledge_api.py"),
  typescriptOnly(
    "knowledge",
    "GET",
    "/api/knowledge/playbook/:scenario",
    "TypeScript exposes direct playbook readback from the interactive server.",
  ),
  both("notebooks", "GET", "/api/notebooks", "autocontext/src/autocontext/server/notebook_api.py"),
  both("notebooks", "GET", "/api/notebooks/:session_id", "autocontext/src/autocontext/server/notebook_api.py"),
  both("notebooks", "PUT", "/api/notebooks/:session_id", "autocontext/src/autocontext/server/notebook_api.py"),
  both("notebooks", "DELETE", "/api/notebooks/:session_id", "autocontext/src/autocontext/server/notebook_api.py"),
  both("monitors", "POST", "/api/monitors", "autocontext/src/autocontext/server/monitor_api.py"),
  both("monitors", "GET", "/api/monitors", "autocontext/src/autocontext/server/monitor_api.py"),
  both("monitors", "DELETE", "/api/monitors/:condition_id", "autocontext/src/autocontext/server/monitor_api.py"),
  both("monitors", "GET", "/api/monitors/alerts", "autocontext/src/autocontext/server/monitor_api.py"),
  both("monitors", "POST", "/api/monitors/:condition_id/wait", "autocontext/src/autocontext/server/monitor_api.py"),

  both(
    "discovery",
    "GET",
    "/",
    PY_APP,
    TS_SERVER,
    "Both runtimes advertise API information from the root response.",
  ),
  typescriptOnly(
    "discovery",
    "GET",
    "/api/capabilities/http",
    "TypeScript exposes this parity matrix for clients that need runtime-aware HTTP discovery.",
  ),
  both(
    "dashboard",
    "GET",
    "/dashboard",
    PY_APP,
    TS_SERVER,
    "Python returns the API-info placeholder; TypeScript serves the lightweight dashboard shell.",
  ),
  typescriptOnly("scenarios", "GET", "/api/scenarios", "TypeScript exposes built-in and custom scenario discovery."),
  typescriptOnly("simulations", "GET", "/api/simulations", "TypeScript exposes simulation catalog routes."),
  typescriptOnly("simulations", "GET", "/api/simulations/:name", "TypeScript exposes simulation detail routes."),
  typescriptOnly(
    "simulations",
    "GET",
    "/api/simulations/:name/dashboard",
    "TypeScript exposes simulation dashboard payload routes.",
  ),
  typescriptOnly("campaigns", "GET", "/api/campaigns", "Campaign orchestration is currently TypeScript-only."),
  typescriptOnly("campaigns", "POST", "/api/campaigns", "Campaign orchestration is currently TypeScript-only."),
  typescriptOnly("campaigns", "GET", "/api/campaigns/:id", "Campaign orchestration is currently TypeScript-only."),
  typescriptOnly(
    "campaigns",
    "GET",
    "/api/campaigns/:id/progress",
    "Campaign orchestration is currently TypeScript-only.",
  ),
  typescriptOnly(
    "campaigns",
    "POST",
    "/api/campaigns/:id/missions",
    "Campaign orchestration is currently TypeScript-only.",
  ),
  typescriptOnly(
    "campaigns",
    "POST",
    "/api/campaigns/:id/:action",
    "Campaign pause, resume, and cancel actions are currently TypeScript-only.",
  ),
  typescriptOnly("missions", "GET", "/api/missions", "Mission planning routes are currently TypeScript-only."),
  typescriptOnly("missions", "GET", "/api/missions/:id", "Mission planning routes are currently TypeScript-only."),
  typescriptOnly(
    "missions",
    "GET",
    "/api/missions/:id/steps",
    "Mission planning routes are currently TypeScript-only.",
  ),
  typescriptOnly(
    "missions",
    "GET",
    "/api/missions/:id/subgoals",
    "Mission planning routes are currently TypeScript-only.",
  ),
  typescriptOnly(
    "missions",
    "GET",
    "/api/missions/:id/budget",
    "Mission planning routes are currently TypeScript-only.",
  ),
  typescriptOnly(
    "missions",
    "GET",
    "/api/missions/:id/artifacts",
    "Mission planning routes are currently TypeScript-only.",
  ),
  typescriptOnly(
    "missions",
    "POST",
    "/api/missions/:id/:action",
    "Mission run, pause, resume, and cancel actions are currently TypeScript-only.",
  ),

  ...pythonOnlyRoutes("cockpit", "autocontext/src/autocontext/server/cockpit_api.py", [
    ["GET", "/api/cockpit/notebooks"],
    ["GET", "/api/cockpit/notebooks/:session_id"],
    ["GET", "/api/cockpit/notebooks/:session_id/effective-context"],
    ["PUT", "/api/cockpit/notebooks/:session_id"],
    ["DELETE", "/api/cockpit/notebooks/:session_id"],
    ["GET", "/api/cockpit/runs"],
    ["GET", "/api/cockpit/runs/:run_id/status"],
    ["GET", "/api/cockpit/runs/:run_id/changelog"],
    ["GET", "/api/cockpit/runs/:run_id/compare/:gen_a/:gen_b"],
    ["GET", "/api/cockpit/runs/:run_id/resume"],
    ["GET", "/api/cockpit/writeup/:run_id"],
    ["POST", "/api/cockpit/runs/:run_id/consult"],
    ["GET", "/api/cockpit/runs/:run_id/consultations"],
  ]),
  ...pythonOnlyRoutes("hub", "autocontext/src/autocontext/server/hub_api.py", [
    ["GET", "/api/hub/sessions"],
    ["GET", "/api/hub/sessions/:session_id"],
    ["PUT", "/api/hub/sessions/:session_id"],
    ["POST", "/api/hub/sessions/:session_id/heartbeat"],
    ["POST", "/api/hub/packages/from-run/:run_id"],
    ["GET", "/api/hub/packages"],
    ["GET", "/api/hub/packages/:package_id"],
    ["POST", "/api/hub/packages/:package_id/adopt"],
    ["POST", "/api/hub/results/from-run/:run_id"],
    ["GET", "/api/hub/results"],
    ["GET", "/api/hub/results/:result_id"],
    ["POST", "/api/hub/promotions"],
    ["GET", "/api/hub/feed"],
  ]),
  ...pythonOnlyRoutes("openclaw", "autocontext/src/autocontext/server/openclaw_api.py", [
    ["POST", "/api/openclaw/evaluate"],
    ["POST", "/api/openclaw/validate"],
    ["POST", "/api/openclaw/artifacts"],
    ["GET", "/api/openclaw/artifacts"],
    ["GET", "/api/openclaw/artifacts/:artifact_id"],
    ["GET", "/api/openclaw/distill"],
    ["POST", "/api/openclaw/distill"],
    ["GET", "/api/openclaw/distill/:job_id"],
    ["PATCH", "/api/openclaw/distill/:job_id"],
    ["GET", "/api/openclaw/capabilities"],
    ["GET", "/api/openclaw/discovery/capabilities"],
    ["GET", "/api/openclaw/discovery/scenario/:scenario_name"],
    ["GET", "/api/openclaw/discovery/health"],
    ["GET", "/api/openclaw/discovery/scenario/:scenario_name/artifacts"],
    ["GET", "/api/openclaw/skill/manifest"],
  ]),
];

function pythonOnlyRoutes(
  domain: string,
  source: string,
  routes: Array<[HttpApiMethod, string]>,
): HttpApiParityEntry[] {
  return routes.map(([method, path]) => pythonOnly(
    domain,
    method,
    path,
    source,
    `${domain} HTTP routes are mounted by the Python FastAPI app and are not yet ported to TypeScript.`,
  ));
}

export function buildHttpApiParityMatrix(): HttpApiParityMatrix {
  const summary: Record<HttpApiParityStatus, number> = {
    aligned: 0,
    typescript_gap: 0,
    python_gap: 0,
  };
  for (const route of HTTP_API_PARITY_ROUTES) {
    summary[route.status] += 1;
  }
  return {
    version: 1,
    runtimes: ["python", "typescript"],
    summary,
    routes: HTTP_API_PARITY_ROUTES.map((route) => ({ ...route })),
  };
}
