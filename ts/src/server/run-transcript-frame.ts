import type { ServerMessage } from "./protocol.js";
import {
  AGENT_PROGRESS_NOTE_EVENT_NAME,
  isAgentProgressNotePayloadRetainable,
  sanitizeAgentProgressNotePayload,
} from "../loop/agent-progress-note.js";
import {
  AGENT_TASK_PLAN_EVENT_NAME,
  isAgentTaskPlanPayloadRetainable,
  sanitizeAgentTaskPlanPayload,
} from "../loop/agent-task-plan.js";
import { PLAYBOOK_UPDATE_SKIPPED_EVENT } from "../loop/playbook-update-events.js";
import {
  REDACTED_PRESENTATION_VALUE,
  redactPresentationText,
} from "../security/presentation-redaction.js";

const TRUNCATED_VALUE = "[Truncated]";
const MAX_TEXT_LENGTH = 4_000;
const MAX_ARRAY_ITEMS = 16;
const MAX_OBJECT_KEYS = 24;
const MAX_VALUE_DEPTH = 4;
const MAX_FALLBACK_FIELD_LENGTH = 256;
export const MAX_RETAINED_MESSAGE_BYTES = 8 * 1_024;

const SENSITIVE_KEY_PARTS = [
  "accesskey",
  "apikey",
  "auth",
  "authorization",
  "bearer",
  "clientsecret",
  "cookie",
  "credential",
  "passphrase",
  "password",
  "privatekey",
  "secret",
  "sessionkey",
  "signature",
  "token",
] as const;

const SAFE_TOKEN_METRIC_KEYS = new Set([
  "inputtokens",
  "outputtokens",
  "tokencount",
  "tokens",
  "totaltokens",
]);

const EVENT_PAYLOAD_FIELDS: Readonly<Record<string, readonly string[]>> = {
  action_detail: [
    "action_id",
    "id",
    "name",
    "action_name",
    "kind",
    "status",
    "role",
    "tool",
    "tool_name",
    "generation",
    "activity_kind",
    "started_at",
    "started_at_ms",
    "completed_at",
    "completed_at_ms",
    "duration_ms",
    "input",
    "output",
    "artifacts",
    "run_id",
  ],
  agents_started: ["run_id", "generation", "roles"],
  curator_completed: ["run_id", "generation", "decision"],
  gate_decided: ["run_id", "generation", "decision", "delta", "reason"],
  generation_completed: [
    "run_id",
    "generation",
    "mean_score",
    "best_score",
    "elo",
    "gate_decision",
    "created_tools",
  ],
  generation_started: ["run_id", "generation"],
  generation_timing: ["run_id", "generation", "elapsed_seconds"],
  match_completed: ["run_id", "generation", "match_index", "score"],
  playbook_pending: ["run_id", "scenario", "generation"],
  [PLAYBOOK_UPDATE_SKIPPED_EVENT]: [
    "run_id",
    "scenario",
    "generation",
    "reason",
    "missing_markers",
    "guard_reason",
  ],
  role_completed: ["run_id", "generation", "role", "latency_ms", "tokens"],
  run_completed: [
    "run_id",
    "generation",
    "completed_generations",
    "best_score",
    "elo",
    "dead_ends_found",
  ],
  run_failed: ["run_id", "generation", "error"],
  run_stopped: ["run_id", "reason", "command_id", "completed_generations", "best_score"],
  run_started: ["run_id", "scenario", "target_generations"],
  tournament_completed: [
    "run_id",
    "generation",
    "mean_score",
    "best_score",
    "wins",
    "losses",
    "dimension_means",
    "dimension_regressions",
  ],
  tournament_started: ["run_id", "generation", "matches"],
};

type PresentationValue =
  string | number | boolean | null | PresentationValue[] | { [key: string]: PresentationValue };

export function sanitizeRunTranscriptText(value: string): string {
  const sanitized = redactPresentationText(value);
  return sanitized.length <= MAX_TEXT_LENGTH
    ? sanitized
    : `${sanitized.slice(0, MAX_TEXT_LENGTH)}…`;
}

export function sanitizeRunTranscriptMessage(message: ServerMessage): ServerMessage | null {
  if (message.type === "event" && message.event === AGENT_PROGRESS_NOTE_EVENT_NAME) {
    const payload = sanitizeAgentProgressNotePayload(message.payload);
    if (!payload || !isAgentProgressNotePayloadRetainable(payload)) return null;
    const safe: ServerMessage = {
      type: "event",
      event: AGENT_PROGRESS_NOTE_EVENT_NAME,
      payload,
    };
    return safe;
  }
  if (message.type === "event" && message.event === AGENT_TASK_PLAN_EVENT_NAME) {
    const payload = sanitizeAgentTaskPlanPayload(message.payload);
    if (!payload || !isAgentTaskPlanPayloadRetainable(payload)) return null;
    const safe: ServerMessage = {
      type: "event",
      event: AGENT_TASK_PLAN_EVENT_NAME,
      payload,
    };
    return safe;
  }
  const safe = sanitizeRunTranscriptMessageInternal(message);
  if (!safe) return null;
  if (Buffer.byteLength(JSON.stringify(safe), "utf-8") <= MAX_RETAINED_MESSAGE_BYTES) {
    return safe;
  }
  return truncateRunTranscriptMessage(safe);
}

function sanitizeRunTranscriptMessageInternal(message: ServerMessage): ServerMessage | null {
  switch (message.type) {
    case "event": {
      if (message.event === "runtime_session_event") {
        return {
          type: "event",
          event: "runtime_session_event",
          payload: sanitizeRuntimeSessionEventPayload(message.payload),
        };
      }
      const allowedFields = Object.prototype.hasOwnProperty.call(
        EVENT_PAYLOAD_FIELDS,
        message.event,
      )
        ? EVENT_PAYLOAD_FIELDS[message.event]
        : undefined;
      return {
        type: "event",
        event: sanitizeRunTranscriptText(message.event),
        payload: allowedFields ? sanitizePayload(message.payload, allowedFields) : {},
      };
    }
    case "state":
      return {
        type: "state",
        paused: message.paused,
        generation: message.generation,
        phase: message.phase ? sanitizeRunTranscriptText(message.phase) : undefined,
      };
    case "run_accepted":
      return {
        type: "run_accepted",
        run_id: sanitizeRunTranscriptText(message.run_id),
        scenario: sanitizeRunTranscriptText(message.scenario),
        generations: message.generations,
      };
    case "ack":
      return {
        type: "ack",
        action: sanitizeRunTranscriptText(message.action),
        decision: message.decision ? sanitizeRunTranscriptText(message.decision) : message.decision,
      };
    case "chat_response":
      return {
        type: "chat_response",
        role: sanitizeRunTranscriptText(message.role),
        text: sanitizeRunTranscriptText(message.text),
      };
    case "error":
      return {
        type: "error",
        message: sanitizeRunTranscriptText(message.message),
      };
    case "monitor_alert":
      return {
        type: "monitor_alert",
        alert_id: sanitizeRunTranscriptText(message.alert_id),
        condition_id: sanitizeRunTranscriptText(message.condition_id),
        condition_name: sanitizeRunTranscriptText(message.condition_name),
        condition_type: sanitizeRunTranscriptText(message.condition_type),
        scope: sanitizeRunTranscriptText(message.scope),
        detail: sanitizeRunTranscriptText(message.detail),
      };
    default:
      return null;
  }
}

function sanitizeRuntimeSessionEventPayload(
  payload: Record<string, unknown>,
): Record<string, PresentationValue> {
  const safe = sanitizePayload(payload, [
    "session_id",
    "parent_session_id",
    "task_id",
    "worker_id",
    "event_count",
    "created_at",
    "updated_at",
  ]);
  const event = isRecord(payload.event) ? payload.event : {};
  const eventTypeValue = event.event_type ?? event.eventType;
  const eventType = typeof eventTypeValue === "string" ? eventTypeValue : "";
  const safeEvent = sanitizePayload(event, [
    "event_id",
    "eventId",
    "event_type",
    "eventType",
    "sequence",
    "timestamp",
    "parent_session_id",
    "parentSessionId",
    "task_id",
    "taskId",
    "worker_id",
    "workerId",
  ]);
  const rawEventPayload = isRecord(event.payload) ? event.payload : {};
  const safeEventPayload = sanitizePayload(rawEventPayload, runtimeSessionPayloadFields(eventType));
  if (
    Boolean(rawEventPayload.error) ||
    rawEventPayload.isError === true ||
    rawEventPayload.is_error === true
  ) {
    safeEventPayload.has_error = true;
  }
  safeEvent.payload = safeEventPayload;
  safe.event = safeEvent;
  return safe;
}

function runtimeSessionPayloadFields(eventType: string): readonly string[] {
  const common = [
    "requestId",
    "request_id",
    "role",
    "status",
    "phase",
    "isError",
    "is_error",
  ];
  switch (eventType) {
    case "prompt_submitted":
      return common;
    case "assistant_message":
      return common;
    case "shell_command":
      return [
        ...common,
        "commandName",
        "command_name",
        "exitCode",
        "exit_code",
        "effectClass",
        "effectOutcome",
      ];
    case "tool_call":
      return [
        ...common,
        "tool",
        "toolName",
        "tool_name",
        "name",
        "effectClass",
        "effectOutcome",
      ];
    case "child_task_started":
    case "child_task_completed":
      return [
        ...common,
        "taskId",
        "task_id",
        "childSessionId",
        "child_session_id",
        "workerId",
        "worker_id",
      ];
    case "compaction":
      return [...common, "summaryArtifactId", "summary_artifact_id"];
    case "component_lifecycle":
      return [
        ...common,
        "componentId",
        "component_id",
        "previousState",
        "previous_state",
        "state",
        "operation",
        "outcome",
      ];
    case "component_graph":
      return [...common, "componentId", "component_id", "operation", "outcome"];
    case "runtime_activation":
      return [...common, "runtime", "mode", "operation", "outcome"];
    default:
      return [
        ...common,
        "tool",
        "taskId",
        "task_id",
        "childSessionId",
        "child_session_id",
      ];
  }
}

function truncateRunTranscriptMessage(message: ServerMessage): ServerMessage | null {
  switch (message.type) {
    case "event":
      return {
        type: "event",
        event: message.event.slice(0, MAX_FALLBACK_FIELD_LENGTH),
        payload: { detail: TRUNCATED_VALUE },
      };
    case "state":
      return {
        type: "state",
        paused: message.paused,
        ...(message.generation === undefined ? {} : { generation: message.generation }),
        ...(message.phase === undefined ? {} : { phase: TRUNCATED_VALUE }),
      };
    case "run_accepted":
      return {
        type: "run_accepted",
        run_id: sanitizeRunTranscriptText(message.run_id).slice(0, MAX_FALLBACK_FIELD_LENGTH),
        scenario: TRUNCATED_VALUE,
        generations: message.generations,
      };
    case "ack":
      return { type: "ack", action: TRUNCATED_VALUE, decision: null };
    case "chat_response":
      return {
        type: "chat_response",
        role: sanitizeRunTranscriptText(message.role).slice(0, MAX_FALLBACK_FIELD_LENGTH),
        text: TRUNCATED_VALUE,
      };
    case "error":
      return { type: "error", message: TRUNCATED_VALUE };
    case "monitor_alert":
      return {
        type: "monitor_alert",
        alert_id: message.alert_id.slice(0, MAX_FALLBACK_FIELD_LENGTH),
        condition_id: message.condition_id.slice(0, MAX_FALLBACK_FIELD_LENGTH),
        condition_name: message.condition_name.slice(0, MAX_FALLBACK_FIELD_LENGTH),
        condition_type: message.condition_type.slice(0, MAX_FALLBACK_FIELD_LENGTH),
        scope: message.scope.slice(0, MAX_FALLBACK_FIELD_LENGTH),
        detail: TRUNCATED_VALUE,
      };
    default:
      return null;
  }
}

function sanitizePayload(
  payload: Record<string, unknown>,
  allowedFields: readonly string[],
): Record<string, PresentationValue> {
  const allowed = new Set(allowedFields);
  const result: Record<string, PresentationValue> = Object.create(null);
  for (const [key, value] of Object.entries(payload)) {
    if (!allowed.has(key)) continue;
    result[key] = isSensitiveKey(key) ? REDACTED_PRESENTATION_VALUE : sanitizeValue(value, 0);
  }
  return result;
}

function sanitizeValue(value: unknown, depth: number): PresentationValue {
  if (value === null) return null;
  if (typeof value === "string") return sanitizeRunTranscriptText(value);
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return Number.isFinite(value) ? value : TRUNCATED_VALUE;
  if (depth >= MAX_VALUE_DEPTH) return TRUNCATED_VALUE;
  if (Array.isArray(value)) {
    const sanitized = value.slice(0, MAX_ARRAY_ITEMS).map((item) => sanitizeValue(item, depth + 1));
    if (value.length > MAX_ARRAY_ITEMS) sanitized.push(TRUNCATED_VALUE);
    return sanitized;
  }
  if (!isRecord(value)) return TRUNCATED_VALUE;

  const result: Record<string, PresentationValue> = Object.create(null);
  const entries = Object.entries(value);
  for (const [key, entry] of entries.slice(0, MAX_OBJECT_KEYS)) {
    const sanitizedKey = sanitizeRunTranscriptText(key);
    if (isSensitiveKey(key) || sanitizedKey !== key) {
      result[sanitizedKey === key ? key : REDACTED_PRESENTATION_VALUE] =
        REDACTED_PRESENTATION_VALUE;
      continue;
    }
    result[key] = sanitizeValue(entry, depth + 1);
  }
  if (entries.length > MAX_OBJECT_KEYS) result[TRUNCATED_VALUE] = TRUNCATED_VALUE;
  return result;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isSensitiveKey(value: string): boolean {
  const normalized = value.toLowerCase().replaceAll("-", "").replaceAll("_", "");
  if (SAFE_TOKEN_METRIC_KEYS.has(normalized)) return false;
  return SENSITIVE_KEY_PARTS.some((part) => normalized.includes(part));
}
