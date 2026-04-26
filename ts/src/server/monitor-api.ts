import { randomUUID } from "node:crypto";

import type { MonitorEngine } from "./monitor-engine.js";
import type { SQLiteStore } from "../storage/index.js";

export interface MonitorApiResponse {
  status: number;
  body: unknown;
}

export interface MonitorApiRoutes {
  create(body: Record<string, unknown>): MonitorApiResponse;
  list(query: URLSearchParams): MonitorApiResponse;
  delete(conditionId: string): MonitorApiResponse;
  listAlerts(query: URLSearchParams): MonitorApiResponse;
  wait(conditionId: string, query: URLSearchParams): Promise<MonitorApiResponse>;
}

const CONDITION_TYPES = new Set([
  "metric_threshold",
  "stall_window",
  "artifact_created",
  "process_exit",
  "heartbeat_lost",
]);

export function buildMonitorApiRoutes(opts: {
  openStore: () => SQLiteStore;
  monitorEngine?: MonitorEngine | null;
  defaultHeartbeatTimeoutSeconds: number;
  maxConditions: number;
}): MonitorApiRoutes {
  return {
    create: (body) => {
      const request = parseCreateMonitorRequest(body);
      if (!request.ok) {
        return { status: 422, body: { detail: request.error } };
      }
      if (!CONDITION_TYPES.has(request.conditionType)) {
        return { status: 409, body: { detail: `invalid monitor condition type: ${request.conditionType}` } };
      }
      const params = request.conditionType === "heartbeat_lost"
        && request.params.timeout_seconds === undefined
        ? { ...request.params, timeout_seconds: opts.defaultHeartbeatTimeoutSeconds }
        : request.params;
      const conditionId = randomUUID().replace(/-/g, "");

      if (opts.monitorEngine) {
        try {
          opts.monitorEngine.createCondition({
            id: conditionId,
            name: request.name,
            conditionType: request.conditionType,
            params,
            scope: request.scope,
          });
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error);
          return { status: 409, body: { detail: message } };
        }
        return withStore(opts.openStore, (store) => ({
          status: 201,
          body: store.getMonitorCondition(conditionId) ?? { id: conditionId, name: request.name },
        }));
      }

      return withStore(opts.openStore, (store) => {
        if (store.countMonitorConditions({ activeOnly: true }) >= opts.maxConditions) {
          return {
            status: 409,
            body: { detail: `maximum active monitor conditions reached (${opts.maxConditions})` },
          };
        }
        store.insertMonitorCondition({
          id: conditionId,
          name: request.name,
          conditionType: request.conditionType,
          params,
          scope: request.scope,
        });
        return {
          status: 201,
          body: store.getMonitorCondition(conditionId) ?? { id: conditionId, name: request.name },
        };
      });
    },
    list: (query) => withStore(opts.openStore, (store) => ({
      status: 200,
      body: store.listMonitorConditions({
        activeOnly: readBooleanQuery(query, "active_only", true),
        scope: query.get("scope") ?? undefined,
      }),
    })),
    delete: (conditionId) => withStore(opts.openStore, (store) => {
      const found = store.deactivateMonitorCondition(conditionId);
      if (!found) {
        return { status: 404, body: { detail: "Monitor condition not found" } };
      }
      return { status: 204, body: null };
    }),
    listAlerts: (query) => withStore(opts.openStore, (store) => ({
      status: 200,
      body: store.listMonitorAlerts({
        conditionId: query.get("condition_id") ?? undefined,
        scope: query.get("scope") ?? undefined,
        limit: readIntegerQuery(query, "limit", 100),
        since: query.get("since") ?? undefined,
      }),
    })),
    wait: async (conditionId, query) => {
      if (!opts.monitorEngine) {
        return {
          status: 503,
          body: { detail: "Monitor engine not available" },
        };
      }
      const timeout = readNumberQuery(query, "timeout", 30);
      const alert = await opts.monitorEngine.waitForAlert(conditionId, timeout);
      return {
        status: 200,
        body: {
          fired: alert !== null,
          alert,
        },
      };
    },
  };
}

function withStore(
  openStore: () => SQLiteStore,
  fn: (store: SQLiteStore) => MonitorApiResponse,
): MonitorApiResponse {
  const store = openStore();
  try {
    return fn(store);
  } finally {
    store.close();
  }
}

type CreateMonitorRequestResult =
  | {
    ok: true;
    name: string;
    conditionType: string;
    params: Record<string, unknown>;
    scope: string;
  }
  | { ok: false; error: string };

function parseCreateMonitorRequest(body: Record<string, unknown>): CreateMonitorRequestResult {
  if (typeof body.name !== "string") {
    return { ok: false, error: "name is required" };
  }
  if (typeof body.condition_type !== "string") {
    return { ok: false, error: "condition_type is required" };
  }
  if (body.params !== undefined && !isRecord(body.params)) {
    return { ok: false, error: "params must be an object" };
  }
  if (body.scope !== undefined && typeof body.scope !== "string") {
    return { ok: false, error: "scope must be a string" };
  }
  return {
    ok: true,
    name: body.name,
    conditionType: body.condition_type,
    params: body.params === undefined ? {} : body.params,
    scope: typeof body.scope === "string" ? body.scope : "global",
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function readBooleanQuery(query: URLSearchParams, key: string, fallback: boolean): boolean {
  const value = query.get(key);
  if (value === null) return fallback;
  if (["false", "0", "no"].includes(value.toLowerCase())) return false;
  if (["true", "1", "yes"].includes(value.toLowerCase())) return true;
  return fallback;
}

function readIntegerQuery(query: URLSearchParams, key: string, fallback: number): number {
  const value = query.get(key);
  if (value === null) return fallback;
  const parsed = Number.parseInt(value, 10);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback;
}

function readNumberQuery(query: URLSearchParams, key: string, fallback: number): number {
  const value = query.get(key);
  if (value === null) return fallback;
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : fallback;
}
