import type { ServerMessage } from "./protocol.js";

const REDACTED_VALUE = "[Redacted]";
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

const AUTHORIZATION_PATTERN =
  /\b(?:authorization|proxy-authorization)\s*[:=]\s*(?:bearer\s+)?[^\s,;]+/gi;
const CREDENTIAL_ASSIGNMENT_PATTERN =
  /\b(?:api[_-]?key|access[_-]?key|client[_-]?secret|refresh[_-]?token|session[_-]?(?:key|token)|token|secret|password|passphrase|cookie|credential)\b\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s,;]+)/gi;
const QUERY_CREDENTIAL_PATTERN =
  /([?&](?:api[_-]?key|access[_-]?key|client[_-]?secret|refresh[_-]?token|session[_-]?(?:key|token)|token|secret|password|passphrase|signature)=)[^&#\s]+/gi;
const URL_USERINFO_PATTERN = /(https?:\/\/)[^/@\s]+@/gi;
const BARE_BEARER_PATTERN =
  /\bbearer\s+[A-Za-z0-9._~+/-]{12,}={0,2}(?=\s|$|[,;])/gi;
const JWT_PATTERN = /\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b/g;
const PRIVATE_KEY_PATTERN =
  /-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g;
const DASHED_PROVIDER_TOKEN_PATTERN = /\b(?:sk|pk)-[A-Za-z0-9_-]{8,}\b/g;
const UNDERSCORED_PROVIDER_TOKEN_PATTERN = /\b(?:gsk|sk|pk)_[A-Za-z0-9_-]{8,}\b/g;
const GOOGLE_API_KEY_PATTERN = /\bAIza[0-9A-Za-z_-]{20,}\b/g;
const AWS_ACCESS_KEY_PATTERN =
  /\b(?:A3T[A-Z0-9]|AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASCA)[A-Z0-9]{16}\b/g;
const GITHUB_TOKEN_PATTERN = /\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b/g;
const GITHUB_PAT_PATTERN = /\bgithub_pat_[A-Za-z0-9_]{20,}\b/g;
const GITLAB_TOKEN_PATTERN = /\bglpat-[A-Za-z0-9_-]{12,}\b/g;
const SLACK_TOKEN_PATTERN = /\bxox[baprs]-[A-Za-z0-9-]{10,}\b/g;

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
  const sanitized = value
    .replace(AUTHORIZATION_PATTERN, REDACTED_VALUE)
    .replace(CREDENTIAL_ASSIGNMENT_PATTERN, REDACTED_VALUE)
    .replace(QUERY_CREDENTIAL_PATTERN, `$1${REDACTED_VALUE}`)
    .replace(URL_USERINFO_PATTERN, "$1[Redacted]@")
    .replace(BARE_BEARER_PATTERN, REDACTED_VALUE)
    .replace(JWT_PATTERN, REDACTED_VALUE)
    .replace(PRIVATE_KEY_PATTERN, REDACTED_VALUE)
    .replace(DASHED_PROVIDER_TOKEN_PATTERN, REDACTED_VALUE)
    .replace(UNDERSCORED_PROVIDER_TOKEN_PATTERN, REDACTED_VALUE)
    .replace(GOOGLE_API_KEY_PATTERN, REDACTED_VALUE)
    .replace(AWS_ACCESS_KEY_PATTERN, REDACTED_VALUE)
    .replace(GITHUB_TOKEN_PATTERN, REDACTED_VALUE)
    .replace(GITHUB_PAT_PATTERN, REDACTED_VALUE)
    .replace(GITLAB_TOKEN_PATTERN, REDACTED_VALUE)
    .replace(SLACK_TOKEN_PATTERN, REDACTED_VALUE);
  return sanitized.length <= MAX_TEXT_LENGTH
    ? sanitized
    : `${sanitized.slice(0, MAX_TEXT_LENGTH)}…`;
}

export function sanitizeRunTranscriptMessage(message: ServerMessage): ServerMessage | null {
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
    result[key] = isSensitiveKey(key) ? REDACTED_VALUE : sanitizeValue(value, 0);
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
      result[sanitizedKey === key ? key : REDACTED_VALUE] = REDACTED_VALUE;
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
