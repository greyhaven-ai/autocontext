import type { RuntimeSessionEventLog } from "./runtime-events.js";
import { runtimeSessionIdForRun } from "./runtime-session-ids.js";

export interface RuntimeSessionSummary {
  session_id: string;
  parent_session_id: string;
  task_id: string;
  worker_id: string;
  goal: string;
  event_count: number;
  created_at: string;
  updated_at: string;
}

export interface RuntimeSessionReadStore {
  list(opts?: { limit?: number }): RuntimeSessionEventLog[];
  load(sessionId: string): RuntimeSessionEventLog | null;
}

export function summarizeRuntimeSession(log: RuntimeSessionEventLog): RuntimeSessionSummary {
  return {
    session_id: log.sessionId,
    parent_session_id: log.parentSessionId,
    task_id: log.taskId,
    worker_id: log.workerId,
    goal: readMetadataString(log.metadata, "goal"),
    event_count: log.events.length,
    created_at: log.createdAt,
    updated_at: log.updatedAt || log.createdAt,
  };
}

export function readRuntimeSessionSummaries(
  store: RuntimeSessionReadStore,
  opts: { limit?: number } = {},
): RuntimeSessionSummary[] {
  return store.list({ limit: opts.limit }).map(summarizeRuntimeSession);
}

export function readRuntimeSessionById(
  store: RuntimeSessionReadStore,
  sessionId: string,
): RuntimeSessionEventLog | null {
  return store.load(sessionId);
}

export function readRuntimeSessionByRunId(
  store: RuntimeSessionReadStore,
  runId: string,
): RuntimeSessionEventLog | null {
  return store.load(runtimeSessionIdForRun(runId));
}

function readMetadataString(
  metadata: Record<string, unknown>,
  key: string,
): string {
  const value = metadata[key];
  return typeof value === "string" ? value : "";
}
