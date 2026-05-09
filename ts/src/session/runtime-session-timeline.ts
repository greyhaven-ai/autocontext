import type { RuntimeSessionEvent, RuntimeSessionEventLog } from "./runtime-events.js";
import { RuntimeSessionEventType } from "./runtime-events.js";
import { runtimeSessionIdForRun } from "./runtime-session-ids.js";
import {
  summarizeRuntimeSession,
  type RuntimeSessionReadStore,
  type RuntimeSessionSummary,
} from "./runtime-session-read-model.js";

export type RuntimeSessionTimelineItem =
  | RuntimeSessionPromptTimelineItem
  | RuntimeSessionChildTaskTimelineItem
  | RuntimeSessionGenericTimelineItem;

export interface RuntimeSessionTimeline {
  summary: RuntimeSessionSummary;
  items: RuntimeSessionTimelineItem[];
  item_count: number;
  in_flight_count: number;
  error_count: number;
}

export interface RuntimeSessionPromptTimelineItem {
  kind: "prompt";
  status: "in_flight" | "completed" | "failed";
  sequence_start: number;
  sequence_end: number | null;
  started_at: string;
  completed_at: string | null;
  role: string;
  cwd: string;
  prompt_preview: string;
  response_preview: string;
  error: string;
  request_id: string;
  prompt_event_id: string;
  response_event_id: string;
}

export interface RuntimeSessionChildTaskTimelineItem {
  kind: "child_task";
  status: "started" | "completed" | "failed";
  sequence_start: number;
  sequence_end: number | null;
  started_at: string;
  completed_at: string | null;
  task_id: string;
  child_session_id: string;
  worker_id: string;
  role: string;
  cwd: string;
  depth: number | null;
  max_depth: number | null;
  result_preview: string;
  error: string;
}

export interface RuntimeSessionGenericTimelineItem {
  kind: "event";
  sequence: number;
  event_id: string;
  event_type: string;
  timestamp: string;
  title: string;
  details: Record<string, string | number | boolean>;
}

export function buildRuntimeSessionTimeline(log: RuntimeSessionEventLog): RuntimeSessionTimeline {
  const items: RuntimeSessionTimelineItem[] = [];
  const openPrompts: RuntimeSessionPromptTimelineItem[] = [];
  const promptsByRequestId = new Map<string, RuntimeSessionPromptTimelineItem>();
  const promptsByEventId = new Map<string, RuntimeSessionPromptTimelineItem>();
  const childTasks = new Map<string, RuntimeSessionChildTaskTimelineItem>();

  for (const event of log.events) {
    switch (event.eventType) {
      case RuntimeSessionEventType.PROMPT_SUBMITTED: {
        const item = promptItemFromEvent(event);
        openPrompts.push(item);
        promptsByEventId.set(item.prompt_event_id, item);
        if (item.request_id) {
          promptsByRequestId.set(item.request_id, item);
        }
        items.push(item);
        break;
      }
      case RuntimeSessionEventType.ASSISTANT_MESSAGE: {
        const prompt = findPromptForResponse(event, {
          openPrompts,
          promptsByEventId,
          promptsByRequestId,
        });
        if (prompt) {
          completePromptItem(prompt, event);
        } else {
          items.push(genericItemFromEvent(event));
        }
        break;
      }
      case RuntimeSessionEventType.CHILD_TASK_STARTED: {
        const item = childTaskItemFromStartedEvent(event);
        for (const key of childTaskCorrelationKeysFromEvent(event)) {
          childTasks.set(key, item);
        }
        items.push(item);
        break;
      }
      case RuntimeSessionEventType.CHILD_TASK_COMPLETED: {
        const item = findChildTaskForCompletion(event, childTasks);
        if (item) {
          completeChildTaskItem(item, event);
        } else {
          items.push(genericItemFromEvent(event));
        }
        break;
      }
      default:
        items.push(genericItemFromEvent(event));
        break;
    }
  }

  return {
    summary: summarizeRuntimeSession(log),
    items,
    item_count: items.length,
    in_flight_count: items.filter(isInFlightItem).length,
    error_count: items.filter(isErrorItem).length,
  };
}

export function readRuntimeSessionTimelineById(
  store: RuntimeSessionReadStore,
  sessionId: string,
): RuntimeSessionTimeline | null {
  const log = store.load(sessionId);
  return log ? buildRuntimeSessionTimeline(log) : null;
}

export function readRuntimeSessionTimelineByRunId(
  store: RuntimeSessionReadStore,
  runId: string,
): RuntimeSessionTimeline | null {
  return readRuntimeSessionTimelineById(store, runtimeSessionIdForRun(runId));
}

function promptItemFromEvent(event: RuntimeSessionEvent): RuntimeSessionPromptTimelineItem {
  return {
    kind: "prompt",
    status: "in_flight",
    sequence_start: event.sequence,
    sequence_end: null,
    started_at: event.timestamp,
    completed_at: null,
    role: readString(event.payload.role),
    cwd: readString(event.payload.cwd),
    prompt_preview: preview(event.payload.prompt),
    response_preview: "",
    error: "",
    request_id: readString(event.payload.requestId),
    prompt_event_id: event.eventId,
    response_event_id: "",
  };
}

function findPromptForResponse(
  event: RuntimeSessionEvent,
  state: {
    openPrompts: RuntimeSessionPromptTimelineItem[];
    promptsByEventId: Map<string, RuntimeSessionPromptTimelineItem>;
    promptsByRequestId: Map<string, RuntimeSessionPromptTimelineItem>;
  },
): RuntimeSessionPromptTimelineItem | undefined {
  const requestId = readString(event.payload.requestId);
  const promptEventId = readString(event.payload.promptEventId);
  const prompt = (requestId ? state.promptsByRequestId.get(requestId) : undefined)
    ?? (promptEventId ? state.promptsByEventId.get(promptEventId) : undefined);
  if (!prompt && (requestId || promptEventId)) {
    return undefined;
  }
  const matchedPrompt = prompt ?? state.openPrompts[0];
  if (!matchedPrompt) {
    return undefined;
  }

  state.promptsByEventId.delete(matchedPrompt.prompt_event_id);
  if (matchedPrompt.request_id) {
    state.promptsByRequestId.delete(matchedPrompt.request_id);
  }
  const idx = state.openPrompts.indexOf(matchedPrompt);
  if (idx !== -1) {
    state.openPrompts.splice(idx, 1);
  }
  return matchedPrompt;
}

function completePromptItem(
  item: RuntimeSessionPromptTimelineItem,
  event: RuntimeSessionEvent,
): void {
  const error = readString(event.payload.error);
  const isError = readBoolean(event.payload.isError) || error !== "";
  item.status = isError ? "failed" : "completed";
  item.sequence_end = event.sequence;
  item.completed_at = event.timestamp;
  item.response_preview = preview(event.payload.text);
  item.error = error;
  item.response_event_id = event.eventId;
  item.role ||= readString(event.payload.role);
  item.cwd ||= readString(event.payload.cwd);
}

function childTaskItemFromStartedEvent(event: RuntimeSessionEvent): RuntimeSessionChildTaskTimelineItem {
  return {
    kind: "child_task",
    status: "started",
    sequence_start: event.sequence,
    sequence_end: null,
    started_at: event.timestamp,
    completed_at: null,
    task_id: readString(event.payload.taskId),
    child_session_id: readString(event.payload.childSessionId),
    worker_id: readString(event.payload.workerId),
    role: readString(event.payload.role),
    cwd: readString(event.payload.cwd),
    depth: readNullableNumber(event.payload.depth),
    max_depth: readNullableNumber(event.payload.maxDepth),
    result_preview: "",
    error: "",
  };
}

function findChildTaskForCompletion(
  event: RuntimeSessionEvent,
  childTasks: Map<string, RuntimeSessionChildTaskTimelineItem>,
): RuntimeSessionChildTaskTimelineItem | undefined {
  for (const key of childTaskCorrelationKeysFromEvent(event)) {
    const item = childTasks.get(key);
    if (item) return item;
  }
  return undefined;
}

function childTaskCorrelationKeysFromEvent(event: RuntimeSessionEvent): string[] {
  const keys: string[] = [];
  const childSessionId = readString(event.payload.childSessionId);
  if (childSessionId) keys.push(`child:${childSessionId}`);
  const taskId = readString(event.payload.taskId);
  const workerId = readString(event.payload.workerId);
  if (taskId && workerId) keys.push(`task:${taskId}:worker:${workerId}`);
  if (taskId) keys.push(`task:${taskId}`);
  if (keys.length === 0) keys.push(`event:${event.eventId}`);
  return keys;
}

function completeChildTaskItem(
  item: RuntimeSessionChildTaskTimelineItem,
  event: RuntimeSessionEvent,
): void {
  const error = readString(event.payload.error);
  const isError = readBoolean(event.payload.isError) || error !== "";
  item.status = isError ? "failed" : "completed";
  item.sequence_end = event.sequence;
  item.completed_at = event.timestamp;
  item.result_preview = preview(event.payload.result);
  item.error = error;
  item.child_session_id ||= readString(event.payload.childSessionId);
  item.worker_id ||= readString(event.payload.workerId);
  item.role ||= readString(event.payload.role);
  item.cwd ||= readString(event.payload.cwd);
}

function genericItemFromEvent(event: RuntimeSessionEvent): RuntimeSessionGenericTimelineItem {
  const details = eventDetails(event.payload);
  const titleDetails = eventTitleDetails(details);
  const detailText = Object.entries(titleDetails)
    .map(([key, value]) => `${key}=${String(value)}`)
    .join(" ");
  return {
    kind: "event",
    sequence: event.sequence,
    event_id: event.eventId,
    event_type: event.eventType,
    timestamp: event.timestamp,
    title: `${event.eventType}${detailText ? ` ${detailText}` : ""}`,
    details,
  };
}

function eventTitleDetails(
  details: Record<string, string | number | boolean>,
): Record<string, string | number | boolean> {
  const titleDetails: Record<string, string | number | boolean> = {};
  for (const key of [
    "command",
    "tool",
    "exitCode",
    "taskId",
    "childSessionId",
    "entryId",
    "entryCount",
    "components",
  ]) {
    const value = details[key];
    if (value !== undefined) titleDetails[key] = value;
  }
  return titleDetails;
}

function eventDetails(payload: Record<string, unknown>): Record<string, string | number | boolean> {
  const details: Record<string, string | number | boolean> = {};
  for (const key of [
    "role",
    "cwd",
    "command",
    "tool",
    "exitCode",
    "taskId",
    "childSessionId",
    "entryId",
    "entryCount",
    "components",
    "ledgerPath",
    "generation",
  ]) {
    const value = payload[key];
    if (typeof value === "string" && value !== "") details[key] = preview(value);
    if (typeof value === "number" || typeof value === "boolean") details[key] = value;
  }
  const commandName = aliasedDetail(payload, "commandName");
  if (details.command === undefined && commandName !== undefined) {
    details.command = commandName;
  }
  const toolName = aliasedDetail(payload, "toolName");
  if (details.tool === undefined && toolName !== undefined) {
    details.tool = toolName;
  }
  return details;
}

function aliasedDetail(
  payload: Record<string, unknown>,
  key: string,
): string | undefined {
  const value = payload[key];
  return typeof value === "string" && value !== "" ? preview(value) : undefined;
}

function isInFlightItem(item: RuntimeSessionTimelineItem): boolean {
  return (item.kind === "prompt" && item.status === "in_flight")
    || (item.kind === "child_task" && item.status === "started");
}

function isErrorItem(item: RuntimeSessionTimelineItem): boolean {
  return (item.kind === "prompt" && item.status === "failed")
    || (item.kind === "child_task" && item.status === "failed");
}

function preview(value: unknown, maxLength = 240): string {
  if (value === undefined || value === null) return "";
  const raw = typeof value === "string" ? value : JSON.stringify(value);
  const normalized = raw.replace(/\s+/g, " ").trim();
  return normalized.length > maxLength ? `${normalized.slice(0, maxLength - 3)}...` : normalized;
}

function readString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function readBoolean(value: unknown): boolean {
  return typeof value === "boolean" ? value : false;
}

function readNullableNumber(value: unknown): number | null {
  return typeof value === "number" ? value : null;
}
