import type { RuntimeSessionEventLog } from "../session/runtime-events.js";
import { runtimeSessionIdForRun } from "../session/runtime-session-ids.js";
import {
  summarizeRuntimeSession,
  type RuntimeSessionReadStore,
  type RuntimeSessionSummary,
} from "../session/runtime-session-read-model.js";
import {
  buildRuntimeSessionTimeline,
  type RuntimeSessionTimeline,
  type RuntimeSessionTimelineItem,
} from "../session/runtime-session-timeline.js";

export { summarizeRuntimeSession } from "../session/runtime-session-read-model.js";
export type {
  RuntimeSessionReadStore,
  RuntimeSessionSummary,
} from "../session/runtime-session-read-model.js";

export const RUNTIME_SESSIONS_HELP_TEXT = `autoctx runtime-sessions — Inspect recorded runtime sessions

Usage:
  autoctx runtime-sessions list [--limit N] [--json]
  autoctx runtime-sessions show <session-id> [--json]
  autoctx runtime-sessions show --id <session-id> [--json]
  autoctx runtime-sessions show --run-id <run-id> [--json]
  autoctx runtime-sessions timeline <session-id> [--json]
  autoctx runtime-sessions timeline --run-id <run-id> [--json]

Options:
  --limit N            Maximum number of sessions to show (default: 50)
  --id <session-id>    Session id for show
  --run-id <run-id>    Resolve the run-scoped runtime session id
  --json               Output machine-readable JSON

See also: run, list, status`;

export interface RuntimeSessionsCommandValues {
  id?: string;
  "run-id"?: string;
  limit?: string;
  json?: boolean;
}

export type RuntimeSessionsCommandPlan =
  | { action: "list"; limit: number; json: boolean }
  | { action: "show"; sessionId: string; json: boolean }
  | { action: "timeline"; sessionId: string; json: boolean };

export function planRuntimeSessionsCommand(
  values: RuntimeSessionsCommandValues,
  positionals: string[] = [],
): RuntimeSessionsCommandPlan {
  const [subcommand, maybeSessionId, ...extra] = positionals;
  const action = subcommand ?? "list";
  if (extra.length > 0) {
    throw new Error(`Unexpected runtime-sessions arguments: ${extra.join(" ")}`);
  }
  if (action === "list") {
    if (maybeSessionId) {
      throw new Error(`Unexpected runtime-sessions list argument: ${maybeSessionId}`);
    }
    return {
      action: "list",
      limit: parseLimit(values.limit),
      json: !!values.json,
    };
  }
  if (action === "show") {
    const sessionId = resolveShowSessionId(values, maybeSessionId);
    if (!sessionId) {
      throw new Error("runtime-sessions show requires a session id");
    }
    return {
      action: "show",
      sessionId,
      json: !!values.json,
    };
  }
  if (action === "timeline") {
    const sessionId = resolveShowSessionId(values, maybeSessionId);
    if (!sessionId) {
      throw new Error("runtime-sessions timeline requires a session id");
    }
    return {
      action: "timeline",
      sessionId,
      json: !!values.json,
    };
  }
  throw new Error(`Unknown runtime-sessions action: ${action}`);
}

function resolveShowSessionId(
  values: RuntimeSessionsCommandValues,
  positionalSessionId: string | undefined,
): string {
  const provided = [
    values.id ? "id" : "",
    values["run-id"] ? "run-id" : "",
    positionalSessionId ? "positional session id" : "",
  ].filter(Boolean);
  if (provided.length > 1) {
    throw new Error(
      "runtime-sessions show accepts only one of <session-id>, --id, or --run-id",
    );
  }
  if (values["run-id"]) {
    return runtimeSessionIdForRun(values["run-id"]);
  }
  return values.id ?? positionalSessionId ?? "";
}

export function renderRuntimeSessionList(
  sessions: RuntimeSessionSummary[],
  json: boolean,
): string {
  if (json) {
    return JSON.stringify({ sessions }, null, 2);
  }
  if (sessions.length === 0) {
    return "No runtime sessions found.";
  }
  return sessions
    .map((session) => {
      const goal = session.goal || "(none)";
      return `${session.session_id}  events=${session.event_count}  goal=${goal}  updated=${session.updated_at}`;
    })
    .join("\n");
}

export function renderRuntimeSessionShow(
  log: RuntimeSessionEventLog,
  json: boolean,
): string {
  if (json) {
    return JSON.stringify(log.toJSON(), null, 2);
  }
  const summary = summarizeRuntimeSession(log);
  const lines = [
    `Runtime session ${summary.session_id}`,
    `Goal: ${summary.goal || "(none)"}`,
    `Events: ${summary.event_count}`,
    `Created: ${summary.created_at}`,
    `Updated: ${summary.updated_at}`,
  ];
  if (summary.parent_session_id) lines.push(`Parent: ${summary.parent_session_id}`);
  if (summary.task_id) lines.push(`Task: ${summary.task_id}`);
  if (summary.worker_id) lines.push(`Worker: ${summary.worker_id}`);
  if (log.events.length > 0) {
    lines.push("", "Event log:");
    for (const event of log.events) {
      const details = payloadSummary(event.payload);
      lines.push(
        `${event.sequence}  ${event.eventType}${details ? `  ${details}` : ""}`,
      );
    }
  }
  return lines.join("\n");
}

export function renderRuntimeSessionTimeline(
  timeline: RuntimeSessionTimeline,
  json: boolean,
): string {
  if (json) {
    return JSON.stringify(timeline, null, 2);
  }
  const lines = [
    `Runtime session timeline ${timeline.summary.session_id}`,
    `Goal: ${timeline.summary.goal || "(none)"}`,
    `Items: ${timeline.item_count}`,
  ];
  if (timeline.in_flight_count > 0) lines.push(`In flight: ${timeline.in_flight_count}`);
  if (timeline.error_count > 0) lines.push(`Errors: ${timeline.error_count}`);
  if (timeline.items.length > 0) {
    lines.push("", "Timeline:");
    for (const item of timeline.items) {
      lines.push(renderTimelineItem(item));
    }
  }
  return lines.join("\n");
}

export function executeRuntimeSessionsCommandWorkflow(opts: {
  plan: RuntimeSessionsCommandPlan;
  store: RuntimeSessionReadStore;
}): string {
  if (opts.plan.action === "list") {
    const sessions = opts.store
      .list({ limit: opts.plan.limit })
      .map(summarizeRuntimeSession);
    return renderRuntimeSessionList(sessions, opts.plan.json);
  }

  const log = opts.store.load(opts.plan.sessionId);
  if (!log) {
    throw new Error(`Runtime session not found: ${opts.plan.sessionId}`);
  }
  if (opts.plan.action === "timeline") {
    return renderRuntimeSessionTimeline(buildRuntimeSessionTimeline(log), opts.plan.json);
  }
  return renderRuntimeSessionShow(log, opts.plan.json);
}

function parseLimit(raw: string | undefined): number {
  const parsed = Number.parseInt(raw ?? "50", 10);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    throw new Error("--limit must be a positive integer");
  }
  return parsed;
}

function payloadSummary(payload: Record<string, unknown>): string {
  return [
    ["role", payload.role],
    ["prompt", payload.prompt],
    ["text", payload.text],
    ["command", payload.command],
    ["tool", payload.tool],
    ["taskId", payload.taskId],
    ["childSessionId", payload.childSessionId],
  ]
    .map(([key, value]) => formatPayloadField(String(key), value))
    .filter((field): field is string => field !== "")
    .join("  ");
}

function formatPayloadField(key: string, value: unknown): string {
  if (value === undefined || value === null || value === "") return "";
  if (typeof value === "string") return `${key}=${truncateInline(value)}`;
  if (typeof value === "number" || typeof value === "boolean") {
    return `${key}=${String(value)}`;
  }
  return `${key}=${truncateInline(JSON.stringify(value))}`;
}

function truncateInline(value: string): string {
  const normalized = value.replace(/\s+/g, " ").trim();
  return normalized.length > 120 ? `${normalized.slice(0, 117)}...` : normalized;
}

function renderTimelineItem(item: RuntimeSessionTimelineItem): string {
  if (item.kind === "prompt") {
    return [
      timelineRange(item.sequence_start, item.sequence_end),
      "prompt",
      item.status,
      formatPayloadField("role", item.role),
      formatPayloadField("cwd", item.cwd),
      formatPayloadField("prompt", item.prompt_preview),
      formatPayloadField("response", item.response_preview),
      formatPayloadField("error", item.error),
    ].filter(Boolean).join("  ");
  }
  if (item.kind === "child_task") {
    return [
      timelineRange(item.sequence_start, item.sequence_end),
      "child_task",
      item.status,
      formatPayloadField("task", item.task_id),
      formatPayloadField("role", item.role),
      formatPayloadField("result", item.result_preview),
      formatPayloadField("error", item.error),
    ].filter(Boolean).join("  ");
  }
  return [
    String(item.sequence),
    "event",
    item.event_type,
    item.title,
  ].filter(Boolean).join("  ");
}

function timelineRange(start: number, end: number | null): string {
  return end === null ? String(start) : `${start}-${end}`;
}
