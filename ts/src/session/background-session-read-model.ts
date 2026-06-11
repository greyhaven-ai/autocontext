import type { RuntimeSessionEventLog } from "./runtime-events.js";
import type { RunRow, TaskQueueRow } from "../storage/index.js";

export type BackgroundSessionStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "canceled"
  | "skipped"
  | "unknown";

export type BackgroundSessionArtifactKind =
  | "branch"
  | "commit"
  | "pull_request"
  | "screenshot"
  | "report"
  | "trace"
  | "dataset"
  | "verification_result"
  | "file"
  | string;

export interface BackgroundSessionArtifactInput {
  readonly artifact_id?: string;
  readonly kind?: BackgroundSessionArtifactKind;
  readonly label?: string;
  readonly path?: string;
  readonly url?: string;
  readonly metadata?: Record<string, unknown>;
}

export interface BackgroundSessionArtifact {
  readonly artifact_id: string;
  readonly kind: BackgroundSessionArtifactKind;
  readonly label: string;
  readonly path: string;
  readonly url: string;
}

export interface BackgroundSessionSummary {
  readonly session_id: string;
  readonly runtime_session_id: string;
  readonly run_id: string;
  readonly task_id: string;
  readonly parent_session_id: string;
  readonly status: BackgroundSessionStatus;
  readonly goal: string;
  readonly event_count: number;
  readonly artifact_count: number;
  readonly child_session_count: number;
  readonly created_at: string;
  readonly updated_at: string;
  readonly result_url: string;
  readonly runtime_session_url: string;
}

export interface BackgroundSessionDetail {
  readonly summary: BackgroundSessionSummary;
  readonly artifacts: BackgroundSessionArtifact[];
  readonly child_sessions: BackgroundSessionSummary[];
  readonly trigger: Record<string, unknown> | null;
}

export interface BackgroundSessionSource {
  readonly runtimeSession?: RuntimeSessionEventLog | null;
  readonly task?: TaskQueueRow | null;
  readonly run?: RunRow | null;
  readonly artifacts?: BackgroundSessionArtifactInput[] | null;
  readonly childSessions?: RuntimeSessionEventLog[] | null;
}

type NormalizedBackgroundSessionSource = {
  runtimeSession: RuntimeSessionEventLog | null;
  task: TaskQueueRow | null;
  run: RunRow | null;
  artifacts: BackgroundSessionArtifactInput[];
  childSessions: RuntimeSessionEventLog[];
};

export function buildBackgroundSessionSummary(
  source: BackgroundSessionSource,
): BackgroundSessionSummary {
  const normalized = normalizeSource(source);
  const sessionId = readSessionId(normalized);
  const runtimeSessionId = normalized.runtimeSession?.sessionId ?? "";

  return {
    session_id: sessionId,
    runtime_session_id: runtimeSessionId,
    run_id: readMetadataString(normalized.runtimeSession?.metadata, "runId") || normalized.run?.run_id || "",
    task_id: readTaskId(normalized),
    parent_session_id: normalized.runtimeSession?.parentSessionId ?? "",
    status: readBackgroundSessionStatus(normalized),
    goal: readGoal(normalized),
    event_count: normalized.runtimeSession?.events.length ?? 0,
    artifact_count: normalized.artifacts.length,
    child_session_count: normalized.childSessions.length,
    created_at: readCreatedAt(normalized),
    updated_at: readUpdatedAt(normalized),
    result_url: backgroundSessionUrl(sessionId),
    runtime_session_url: runtimeSessionUrl(runtimeSessionId),
  };
}

export function buildBackgroundSessionDetail(
  source: BackgroundSessionSource,
): BackgroundSessionDetail {
  return {
    summary: buildBackgroundSessionSummary(source),
    artifacts: (source.artifacts ?? []).map(sanitizeArtifact),
    child_sessions: (source.childSessions ?? []).map((runtimeSession) =>
      buildBackgroundSessionSummary({ runtimeSession }),
    ),
    trigger: readTrigger(source.task),
  };
}

export function backgroundSessionUrl(sessionId: string): string {
  return sessionId ? `/api/cockpit/background-sessions/${encodeURIComponent(sessionId)}` : "";
}

export function runtimeSessionUrl(sessionId: string): string {
  return sessionId ? `/api/cockpit/runtime-sessions/${encodeURIComponent(sessionId)}` : "";
}

const QUEUED_STATUSES = new Set(["pending", "queued", "scheduled", "backlog"]);
const RUNNING_STATUSES = new Set(["running", "started", "in_progress", "processing"]);
const COMPLETED_STATUSES = new Set(["completed", "complete", "done", "success", "succeeded"]);
const FAILED_STATUSES = new Set(["failed", "failure", "error"]);
const CANCELED_STATUSES = new Set(["canceled", "cancelled"]);

function normalizeSource(source: BackgroundSessionSource): NormalizedBackgroundSessionSource {
  return {
    runtimeSession: source.runtimeSession ?? null,
    task: source.task ?? null,
    run: source.run ?? null,
    artifacts: source.artifacts ?? [],
    childSessions: source.childSessions ?? [],
  };
}

function readSessionId(source: NormalizedBackgroundSessionSource): string {
  return source.runtimeSession?.sessionId || (source.task?.id ? `task:${source.task.id}` : "");
}

function readTaskId(source: NormalizedBackgroundSessionSource): string {
  return source.runtimeSession?.taskId || source.task?.id || "";
}

function readBackgroundSessionStatus(
  source: NormalizedBackgroundSessionSource,
): BackgroundSessionStatus {
  return normalizeStatus(
    readMetadataString(source.runtimeSession?.metadata, "status") ||
      source.task?.status ||
      source.run?.status ||
      "",
    Boolean(source.runtimeSession),
  );
}

function readGoal(source: NormalizedBackgroundSessionSource): string {
  return (
    readMetadataString(source.runtimeSession?.metadata, "goal") ||
    source.task?.spec_name ||
    source.run?.scenario ||
    ""
  );
}

function readCreatedAt(source: NormalizedBackgroundSessionSource): string {
  return source.runtimeSession?.createdAt || source.task?.created_at || source.run?.created_at || "";
}

function readUpdatedAt(source: NormalizedBackgroundSessionSource): string {
  return source.runtimeSession
    ? source.runtimeSession.updatedAt || source.runtimeSession.createdAt
    : source.task?.updated_at ||
        source.task?.created_at ||
        source.run?.updated_at ||
        source.run?.created_at ||
        "";
}

function sanitizeArtifact(artifact: BackgroundSessionArtifactInput): BackgroundSessionArtifact {
  return {
    artifact_id: artifact.artifact_id ?? "",
    kind: artifact.kind ?? "file",
    label: artifact.label ?? "",
    path: artifact.path ?? "",
    url: artifact.url ?? "",
  };
}

function readTrigger(task: TaskQueueRow | null | undefined): Record<string, unknown> | null {
  if (!task?.config_json) return null;
  const parsed = parseRecordJson(task.config_json);
  const trigger = parsed.trigger;
  if (!isRecord(trigger)) return null;
  return sanitizeRecord(trigger);
}

function normalizeStatus(raw: string, hasRuntimeSession: boolean): BackgroundSessionStatus {
  const status = raw.trim().toLowerCase().replace(/-/g, "_");
  if (QUEUED_STATUSES.has(status)) return "queued";
  if (RUNNING_STATUSES.has(status)) return "running";
  if (COMPLETED_STATUSES.has(status)) return "completed";
  if (FAILED_STATUSES.has(status)) return "failed";
  if (CANCELED_STATUSES.has(status)) return "canceled";
  if (status === "skipped") return "skipped";
  return hasRuntimeSession ? "running" : "unknown";
}

function parseRecordJson(json: string): Record<string, unknown> {
  try {
    const parsed: unknown = JSON.parse(json);
    return isRecord(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

function sanitizeRecord(record: Record<string, unknown>): Record<string, unknown> {
  const clean: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(record)) {
    if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
      clean[key] = value;
    }
  }
  return clean;
}

function readMetadataString(
  metadata: Record<string, unknown> | null | undefined,
  key: string,
): string {
  const value = metadata?.[key];
  return typeof value === "string" ? value : "";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
