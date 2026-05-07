export const TUI_ACTIVITY_FILTERS = [
  "all",
  "runtime",
  "prompts",
  "commands",
  "children",
  "errors",
] as const;
export type TuiActivityFilter = (typeof TUI_ACTIVITY_FILTERS)[number];

export const TUI_ACTIVITY_VERBOSITIES = [
  "quiet",
  "normal",
  "verbose",
] as const;
export type TuiActivityVerbosity = (typeof TUI_ACTIVITY_VERBOSITIES)[number];

const TUI_ACTIVITY_FILTER_VALUES = new Set<string>(TUI_ACTIVITY_FILTERS);
const TUI_ACTIVITY_VERBOSITY_VALUES = new Set<string>(TUI_ACTIVITY_VERBOSITIES);

export interface TuiActivitySettings {
  readonly filter: TuiActivityFilter;
  readonly verbosity: TuiActivityVerbosity;
}

export const DEFAULT_TUI_ACTIVITY_SETTINGS: TuiActivitySettings = {
  filter: "all",
  verbosity: "normal",
};

export const TUI_ACTIVITY_USAGE =
  "/activity [status|reset|<all|runtime|prompts|commands|children|errors> [quiet|normal|verbose]]";

type TuiActivityFocus = "run" | "runtime" | "prompt" | "command" | "child";

interface TuiActivitySummary {
  readonly line: string;
  readonly family: "run" | "runtime";
  readonly focus: TuiActivityFocus;
  readonly hasError: boolean;
}

export function summarizeTuiEvent(
  event: string,
  payload: Record<string, unknown>,
  settings: TuiActivitySettings = DEFAULT_TUI_ACTIVITY_SETTINGS,
): string | null {
  const summary = buildTuiActivitySummary(event, payload, settings);
  return summary && shouldShowActivity(summary, settings.filter) ? summary.line : null;
}

export function formatTuiActivitySettings(settings: TuiActivitySettings): string {
  return `activity filter=${settings.filter} verbosity=${settings.verbosity}`;
}

export function parseTuiActivitySettings(
  raw: string,
  current: TuiActivitySettings = DEFAULT_TUI_ACTIVITY_SETTINGS,
): TuiActivitySettings | null {
  const parts = raw.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) {
    return current;
  }
  if (parts.length > 2) {
    return null;
  }

  let filter = current.filter;
  let verbosity = current.verbosity;
  for (const part of parts) {
    if (isTuiActivityFilter(part)) {
      filter = part;
      continue;
    }
    if (isTuiActivityVerbosity(part)) {
      verbosity = part;
      continue;
    }
    return null;
  }
  return { filter, verbosity };
}

function buildTuiActivitySummary(
  event: string,
  payload: Record<string, unknown>,
  settings: TuiActivitySettings,
): TuiActivitySummary | null {
  switch (event) {
    case "run_started":
      return runSummary(
        `run ${payload.run_id as string} started for ${payload.scenario as string}`,
      );
    case "generation_started":
      return runSummary(`generation ${String(payload.generation)} started`);
    case "role_completed":
      return runSummary(`${String(payload.role)} finished in ${String(payload.latency_ms)}ms`);
    case "tournament_completed":
      return runSummary(
        `tournament mean=${Number(payload.mean_score ?? 0).toFixed(3)} best=${Number(payload.best_score ?? 0).toFixed(3)}`,
      );
    case "gate_decided":
      return runSummary(`gate ${String(payload.decision)} (delta=${String(payload.delta ?? "?")})`);
    case "generation_completed":
      return runSummary(`generation ${String(payload.generation)} stored`);
    case "run_completed":
      return runSummary(`run completed after ${String(payload.completed_generations)} generations`);
    case "run_failed":
      return runSummary(`run failed: ${String(payload.error ?? "unknown error")}`, true);
    case "runtime_session_event":
      return summarizeRuntimeSessionEvent(payload, settings);
    default:
      return null;
  }
}

function runSummary(line: string, hasError = false): TuiActivitySummary {
  return {
    line,
    family: "run",
    focus: "run",
    hasError,
  };
}

function shouldShowActivity(
  summary: TuiActivitySummary,
  filter: TuiActivityFilter,
): boolean {
  switch (filter) {
    case "all":
      return true;
    case "runtime":
      return summary.family === "runtime";
    case "prompts":
      return summary.family === "runtime" && summary.focus === "prompt";
    case "commands":
      return summary.family === "runtime" && summary.focus === "command";
    case "children":
      return summary.family === "runtime" && summary.focus === "child";
    case "errors":
      return summary.hasError;
  }
}

function summarizeRuntimeSessionEvent(
  payload: Record<string, unknown>,
  settings: TuiActivitySettings,
): TuiActivitySummary | null {
  const sessionId = readString(payload.session_id) || readString(payload.sessionId);
  const event = readRecord(payload.event);
  const eventType = readString(event.event_type) || readString(event.eventType);
  if (!sessionId || !eventType) {
    return null;
  }

  const sequence = readSequence(event.sequence);
  const eventPayload = readRecord(event.payload);
  const details = runtimeEventDetails(eventType, eventPayload, settings);
  const metadata = runtimeEventMetadata(event, settings);
  const line = [
    "runtime",
    sessionId,
    `#${sequence}`,
    runtimeEventLabel(eventType),
    details,
    metadata,
  ].filter(Boolean).join(" ");
  return {
    line,
    family: "runtime",
    focus: runtimeEventFocus(eventType),
    hasError: runtimeEventHasError(eventType, eventPayload),
  };
}

function runtimeEventLabel(eventType: string): string {
  switch (eventType) {
    case "prompt_submitted":
      return "prompt";
    case "assistant_message":
      return "assistant";
    case "shell_command":
      return "shell";
    case "tool_call":
      return "tool";
    case "child_task_started":
      return "child started";
    case "child_task_completed":
      return "child completed";
    case "compaction":
      return "compaction";
    default:
      return eventType;
  }
}

function runtimeEventFocus(eventType: string): TuiActivityFocus {
  switch (eventType) {
    case "prompt_submitted":
    case "assistant_message":
      return "prompt";
    case "shell_command":
    case "tool_call":
      return "command";
    case "child_task_started":
    case "child_task_completed":
      return "child";
    default:
      return "runtime";
  }
}

function runtimeEventHasError(
  eventType: string,
  payload: Record<string, unknown>,
): boolean {
  return Boolean(payload.error) || eventType === "run_failed";
}

function runtimeEventDetails(
  eventType: string,
  payload: Record<string, unknown>,
  settings: TuiActivitySettings,
): string {
  const maxLength = fieldMaxLength(settings.verbosity);
  switch (eventType) {
    case "prompt_submitted":
      return formatFields(payload, runtimeEventFields([
        ["role", "role"],
        ["prompt", "prompt"],
      ], settings), maxLength);
    case "assistant_message":
      return formatFields(payload, runtimeEventFields([
        ["role", "role"],
        ["text", "text"],
        ["error", "error"],
      ], settings), maxLength);
    case "shell_command":
      return formatFields(payload, [
        ["command", "command"],
        ["exit", "exitCode"],
      ], maxLength);
    case "tool_call":
      return formatFields(payload, [
        ["tool", "tool"],
        ["command", "command"],
      ], maxLength);
    case "child_task_started":
      return formatFields(payload, runtimeEventFields([
        ["task", "taskId"],
        ["child", "childSessionId"],
        ["role", "role"],
      ], settings), maxLength);
    case "child_task_completed":
      return formatFields(payload, runtimeEventFields([
        ["task", "taskId"],
        ["child", "childSessionId"],
        ["result", "result"],
        ["error", "error"],
      ], settings), maxLength);
    default:
      return formatFields(payload, [
        ["role", "role"],
        ["command", "command"],
        ["tool", "tool"],
        ["task", "taskId"],
        ["child", "childSessionId"],
      ], maxLength);
  }
}

function runtimeEventMetadata(
  event: Record<string, unknown>,
  settings: TuiActivitySettings,
): string {
  if (settings.verbosity !== "verbose") {
    return "";
  }
  return formatFields(event, [
    ["ts", "timestamp"],
    ["event", "event_id"],
    ["parent", "parent_session_id"],
    ["task", "task_id"],
    ["worker", "worker_id"],
  ], fieldMaxLength(settings.verbosity));
}

function runtimeEventFields(
  fields: Array<[label: string, key: string]>,
  settings: TuiActivitySettings,
): Array<[label: string, key: string]> {
  if (settings.verbosity !== "quiet") {
    return fields;
  }
  return fields.filter(([label]) => label !== "prompt" && label !== "text" && label !== "child");
}

function formatFields(
  payload: Record<string, unknown>,
  fields: Array<[label: string, key: string]>,
  maxLength: number,
): string {
  return fields
    .map(([label, key]) => formatField(label, payload[key], maxLength))
    .filter((field): field is string => field !== "")
    .join(" ");
}

function formatField(label: string, value: unknown, maxLength: number): string {
  if (value === undefined || value === null || value === "") return "";
  if (typeof value === "string") return `${label}=${truncateInline(value, maxLength)}`;
  if (typeof value === "number" || typeof value === "boolean") {
    return `${label}=${String(value)}`;
  }
  return `${label}=${truncateInline(JSON.stringify(value), maxLength)}`;
}

function truncateInline(value: string, maxLength: number): string {
  const normalized = value.replace(/\s+/g, " ").trim();
  return normalized.length > maxLength
    ? `${normalized.slice(0, Math.max(0, maxLength - 3))}...`
    : normalized;
}

function fieldMaxLength(verbosity: TuiActivityVerbosity): number {
  switch (verbosity) {
    case "quiet":
      return 60;
    case "normal":
      return 120;
    case "verbose":
      return 240;
  }
}

export function isTuiActivityFilter(value: unknown): value is TuiActivityFilter {
  return typeof value === "string" && TUI_ACTIVITY_FILTER_VALUES.has(value);
}

export function isTuiActivityVerbosity(value: unknown): value is TuiActivityVerbosity {
  return typeof value === "string" && TUI_ACTIVITY_VERBOSITY_VALUES.has(value);
}

function readRecord(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {};
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function readSequence(value: unknown): string {
  return typeof value === "number" ? String(value) : "?";
}
