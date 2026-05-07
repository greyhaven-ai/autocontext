import { runtimeSessionIdForRun } from "../session/runtime-session-ids.js";
import {
  readRuntimeSessionById,
  readRuntimeSessionByRunId,
  readRuntimeSessionSummaries,
  summarizeRuntimeSession,
  type RuntimeSessionReadStore,
  type RuntimeSessionSummary,
} from "../session/runtime-session-read-model.js";
import {
  readRuntimeSessionTimelineById,
  readRuntimeSessionTimelineByRunId,
} from "../session/runtime-session-timeline.js";

export interface RuntimeSessionApiResponse {
  status: number;
  body: unknown;
}

export interface RuntimeSessionDiscovery {
  runtime_session: RuntimeSessionSummary | null;
  runtime_session_url: string;
}

export interface RuntimeSessionApiRoutes {
  list(query: URLSearchParams): RuntimeSessionApiResponse;
  getBySessionId(sessionId: string): RuntimeSessionApiResponse;
  getByRunId(runId: string): RuntimeSessionApiResponse;
  getTimelineBySessionId(sessionId: string): RuntimeSessionApiResponse;
  getTimelineByRunId(runId: string): RuntimeSessionApiResponse;
}

type ClosableRuntimeSessionReadStore = RuntimeSessionReadStore & {
  close?: () => void;
};

export function buildRuntimeSessionApiRoutes(opts: {
  openStore: () => ClosableRuntimeSessionReadStore;
}): RuntimeSessionApiRoutes {
  return {
    list: (query) => {
      const limit = readLimit(query);
      if (!limit.ok) {
        return { status: 422, body: { detail: limit.error } };
      }
      return withStore(opts.openStore, (store) => ({
        status: 200,
        body: {
          sessions: readRuntimeSessionSummaries(store, { limit: limit.value }),
        },
      }));
    },
    getBySessionId: (sessionId) => {
      const cleanSessionId = sessionId.trim();
      if (!cleanSessionId) {
        return { status: 422, body: { detail: "session_id is required" } };
      }
      return withStore(opts.openStore, (store) => {
        const log = readRuntimeSessionById(store, cleanSessionId);
        if (!log) {
          return {
            status: 404,
            body: {
              detail: `Runtime session '${cleanSessionId}' not found`,
              session_id: cleanSessionId,
            },
          };
        }
        return { status: 200, body: log.toJSON() };
      });
    },
    getByRunId: (runId) => {
      const cleanRunId = runId.trim();
      if (!cleanRunId) {
        return { status: 422, body: { detail: "run_id is required" } };
      }
      return withStore(opts.openStore, (store) => {
        const log = readRuntimeSessionByRunId(store, cleanRunId);
        if (!log) {
          return {
            status: 404,
            body: {
              detail: `Runtime session for run '${cleanRunId}' not found`,
              session_id: runtimeSessionIdForRun(cleanRunId),
            },
          };
        }
        return { status: 200, body: log.toJSON() };
      });
    },
    getTimelineBySessionId: (sessionId) => {
      const cleanSessionId = sessionId.trim();
      if (!cleanSessionId) {
        return { status: 422, body: { detail: "session_id is required" } };
      }
      return withStore(opts.openStore, (store) => {
        const timeline = readRuntimeSessionTimelineById(store, cleanSessionId);
        if (!timeline) {
          return {
            status: 404,
            body: {
              detail: `Runtime session timeline '${cleanSessionId}' not found`,
              session_id: cleanSessionId,
            },
          };
        }
        return { status: 200, body: timeline };
      });
    },
    getTimelineByRunId: (runId) => {
      const cleanRunId = runId.trim();
      if (!cleanRunId) {
        return { status: 422, body: { detail: "run_id is required" } };
      }
      return withStore(opts.openStore, (store) => {
        const timeline = readRuntimeSessionTimelineByRunId(store, cleanRunId);
        if (!timeline) {
          return {
            status: 404,
            body: {
              detail: `Runtime session timeline for run '${cleanRunId}' not found`,
              session_id: runtimeSessionIdForRun(cleanRunId),
            },
          };
        }
        return { status: 200, body: timeline };
      });
    },
  };
}

export function runtimeSessionUrlForRun(runId: string): string {
  return `/api/cockpit/runs/${encodeURIComponent(runId)}/runtime-session`;
}

export function runtimeSessionDiscoveryForRun(
  store: RuntimeSessionReadStore | null | undefined,
  runId: string,
): RuntimeSessionDiscovery {
  const log = store ? readRuntimeSessionByRunId(store, runId) : null;
  return {
    runtime_session: log ? summarizeRuntimeSession(log) : null,
    runtime_session_url: runtimeSessionUrlForRun(runId),
  };
}

function withStore(
  openStore: () => ClosableRuntimeSessionReadStore,
  fn: (store: RuntimeSessionReadStore) => RuntimeSessionApiResponse,
): RuntimeSessionApiResponse {
  const store = openStore();
  try {
    return fn(store);
  } finally {
    store.close?.();
  }
}

type ReadLimitResult =
  | { ok: true; value: number }
  | { ok: false; error: string };

function readLimit(query: URLSearchParams): ReadLimitResult {
  const raw = query.get("limit");
  if (raw === null || raw.trim() === "") {
    return { ok: true, value: 50 };
  }
  const parsed = Number(raw);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    return { ok: false, error: "limit must be a positive integer" };
  }
  return { ok: true, value: parsed };
}
