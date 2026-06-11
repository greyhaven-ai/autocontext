import {
  buildBackgroundSessionDetail,
  buildBackgroundSessionSummary,
  type BackgroundSessionSummary,
} from "../session/background-session-read-model.js";
import { normalizeBackgroundSessionTimeline } from "../session/background-session-events.js";
import type { RuntimeSessionEventLog } from "../session/runtime-events.js";
import type { RuntimeSessionReadStore } from "../session/runtime-session-read-model.js";
import type { RunRow, TaskQueueRow } from "../storage/index.js";

export interface BackgroundSessionApiResponse {
  status: number;
  body: unknown;
}

export interface BackgroundSessionApiRoutes {
  list(query: URLSearchParams): BackgroundSessionApiResponse;
  getBySessionId(sessionId: string): BackgroundSessionApiResponse;
}

type BackgroundSessionReadStore = RuntimeSessionReadStore & {
  listChildren?: (parentSessionId: string) => RuntimeSessionEventLog[];
  close?: () => void;
};

type BackgroundSessionSourceStore = {
  listTasks?: (opts?: { limit?: number }) => TaskQueueRow[];
  getTask?: (taskId: string) => TaskQueueRow | null;
  getRun?: (runId: string) => RunRow | null;
  close?: () => void;
};

export function buildBackgroundSessionApiRoutes(opts: {
  openStore: () => BackgroundSessionReadStore;
  openSourceStore?: () => BackgroundSessionSourceStore;
}): BackgroundSessionApiRoutes {
  return {
    list: (query) => {
      const limit = readLimit(query);
      if (!limit.ok) {
        return { status: 422, body: { detail: limit.error } };
      }
      return withStores(opts, (runtimeStore, sourceStore) => {
        const runtimeSessions = runtimeStore.list({ limit: limit.value });
        const taskIndex = indexTasks(sourceStore?.listTasks?.({ limit: limit.value }) ?? []);
        const runtimeTaskIds = new Set(runtimeSessions.map((log) => log.taskId).filter(Boolean));
        const summaries = [
          ...runtimeSessions.map((runtimeSession) =>
            summarizeBackgroundSessionFromStore(runtimeStore, sourceStore, taskIndex, runtimeSession),
          ),
          ...[...taskIndex.values()]
            .filter((task) => !runtimeTaskIds.has(task.id))
            .map((task) => buildBackgroundSessionSummary({ task })),
        ];
        return {
          status: 200,
          body: { sessions: sortBackgroundSessionSummaries(summaries).slice(0, limit.value) },
        };
      });
    },
    getBySessionId: (sessionId) => {
      const cleanSessionId = sessionId.trim();
      if (!cleanSessionId) {
        return { status: 422, body: { detail: "session_id is required" } };
      }
      return withStores(opts, (runtimeStore, sourceStore) => {
        const runtimeSession = runtimeStore.load(cleanSessionId);
        if (runtimeSession) {
          const task = taskForRuntimeSession(sourceStore, undefined, runtimeSession);
          const run = runForRuntimeSession(sourceStore, runtimeSession, task);
          const childSessions = listChildren(runtimeStore, runtimeSession.sessionId);
          return {
            status: 200,
            body: {
              ...buildBackgroundSessionDetail({ runtimeSession, task, run, childSessions }),
              normalized_events: normalizeBackgroundSessionTimeline(runtimeSession),
            },
          };
        }
        const taskId = taskIdFromSessionId(cleanSessionId);
        const task = taskId ? sourceStore?.getTask?.(taskId) ?? null : null;
        if (task) {
          return {
            status: 200,
            body: {
              ...buildBackgroundSessionDetail({ task }),
              normalized_events: [],
            },
          };
        }
        return {
          status: 404,
          body: {
            detail: `Background session '${cleanSessionId}' not found`,
            session_id: cleanSessionId,
          },
        };
      });
    },
  };
}

function summarizeBackgroundSessionFromStore(
  runtimeStore: BackgroundSessionReadStore,
  sourceStore: BackgroundSessionSourceStore | null,
  taskIndex: Map<string, TaskQueueRow>,
  runtimeSession: RuntimeSessionEventLog,
): BackgroundSessionSummary {
  const task = taskForRuntimeSession(sourceStore, taskIndex, runtimeSession);
  return buildBackgroundSessionSummary({
    runtimeSession,
    task,
    run: runForRuntimeSession(sourceStore, runtimeSession, task),
    childSessions: listChildren(runtimeStore, runtimeSession.sessionId),
  });
}

function taskForRuntimeSession(
  sourceStore: BackgroundSessionSourceStore | null | undefined,
  taskIndex: Map<string, TaskQueueRow> | undefined,
  runtimeSession: RuntimeSessionEventLog,
): TaskQueueRow | null {
  if (!runtimeSession.taskId) {
    return null;
  }
  return taskIndex?.get(runtimeSession.taskId) ?? sourceStore?.getTask?.(runtimeSession.taskId) ?? null;
}

function runForRuntimeSession(
  sourceStore: BackgroundSessionSourceStore | null | undefined,
  runtimeSession: RuntimeSessionEventLog,
  task: TaskQueueRow | null,
): RunRow | null {
  const runId = readString(runtimeSession.metadata.runId) || readRunIdFromTask(task);
  return runId ? sourceStore?.getRun?.(runId) ?? null : null;
}

function readRunIdFromTask(task: TaskQueueRow | null): string {
  if (!task?.config_json) {
    return "";
  }
  try {
    const parsed: unknown = JSON.parse(task.config_json);
    if (!isRecord(parsed)) {
      return "";
    }
    const runId = parsed.run_id ?? parsed.runId;
    return readString(runId);
  } catch {
    return "";
  }
}

function taskIdFromSessionId(sessionId: string): string {
  return sessionId.startsWith("task:") ? sessionId.slice("task:".length) : "";
}

function indexTasks(tasks: TaskQueueRow[]): Map<string, TaskQueueRow> {
  return new Map(tasks.map((task) => [task.id, task]));
}

function sortBackgroundSessionSummaries(
  summaries: BackgroundSessionSummary[],
): BackgroundSessionSummary[] {
  return [...summaries]
    .sort((left, right) => left.session_id.localeCompare(right.session_id))
    .sort((left, right) => right.created_at.localeCompare(left.created_at))
    .sort((left, right) => sortTimestamp(right).localeCompare(sortTimestamp(left)));
}

function sortTimestamp(summary: BackgroundSessionSummary): string {
  return summary.updated_at || summary.created_at;
}

function listChildren(
  store: BackgroundSessionReadStore,
  parentSessionId: string,
): RuntimeSessionEventLog[] {
  return store.listChildren?.(parentSessionId) ?? [];
}

function withStores(
  opts: {
    openStore: () => BackgroundSessionReadStore;
    openSourceStore?: () => BackgroundSessionSourceStore;
  },
  fn: (
    runtimeStore: BackgroundSessionReadStore,
    sourceStore: BackgroundSessionSourceStore | null,
  ) => BackgroundSessionApiResponse,
): BackgroundSessionApiResponse {
  const runtimeStore = opts.openStore();
  const sourceStore = opts.openSourceStore?.() ?? null;
  try {
    return fn(runtimeStore, sourceStore);
  } finally {
    sourceStore?.close?.();
    runtimeStore.close?.();
  }
}

type ReadLimitResult = { ok: true; value: number } | { ok: false; error: string };

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

function readString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
