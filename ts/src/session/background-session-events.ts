import type {
  RuntimeSessionEvent,
  RuntimeSessionEventLog,
  RuntimeSessionEventType,
} from "./runtime-events.js";

export type NormalizedSessionEventName =
  | "session_created"
  | "session_queued"
  | "executor_starting"
  | "executor_ready"
  | "prompt_queued"
  | "prompt_started"
  | "runtime_event"
  | "artifact_created"
  | "child_session_created"
  | "session_status"
  | "session_completed";

export type NormalizedSessionEventStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "canceled"
  | "skipped"
  | "unknown";

export type NormalizedSessionEventSummaryValue = string | number | boolean;

const SAFE_SANDBOX_CAPABILITY_REASONS = new Set([
  "adapter_error",
  "missing_snapshot_ref",
  "unsupported_prebuild_repo_image",
  "unsupported_resolve_tunnel_ports",
  "unsupported_restore",
  "unsupported_snapshot",
  "unsupported_warm",
]);

export interface NormalizedSessionEvent {
  readonly event_id: string;
  readonly session_id: string;
  readonly sequence: number;
  readonly ts: string;
  readonly event: NormalizedSessionEventName;
  readonly source_event_type:
    | RuntimeSessionEventType
    | "artifact"
    | "lifecycle_hook"
    | "sandbox_capability"
    | "session_status";
  readonly status: NormalizedSessionEventStatus;
  readonly title: string;
  readonly payload_summary: Record<string, NormalizedSessionEventSummaryValue>;
}

export interface LifecycleSessionEventInput {
  readonly sessionId: string;
  readonly sequence: number;
  readonly timestamp: string;
  readonly hook: "setup" | "start" | string;
  readonly phase: "started" | "completed" | "failed" | "timeout" | string;
}

export interface SandboxCapabilitySessionEventInput {
  readonly sessionId: string;
  readonly sequence: number;
  readonly timestamp: string;
  readonly capability: string;
  readonly phase: string;
  readonly bootMode: string;
  readonly provider?: string;
  readonly unsupportedPolicy?: string;
  readonly reason?: string;
  readonly degraded?: boolean;
  readonly error?: string;
}

export interface ArtifactCreatedSessionEventInput {
  readonly sessionId: string;
  readonly sequence: number;
  readonly timestamp: string;
  readonly artifactId: string;
  readonly kind: string;
  readonly label?: string;
  readonly url?: string;
  readonly path?: string;
}

export interface SessionStatusEventInput {
  readonly sessionId: string;
  readonly sequence: number;
  readonly timestamp: string;
  readonly status: NormalizedSessionEventStatus;
  readonly reason?: string;
}

export function normalizeBackgroundSessionTimeline(
  log: RuntimeSessionEventLog,
  opts: { childLogs?: readonly RuntimeSessionEventLog[] } = {},
): NormalizedSessionEvent[] {
  const events = [...log.events]
    .sort((left, right) => left.sequence - right.sequence)
    .map(normalizeRuntimeSessionEvent);
  for (const childLog of opts.childLogs ?? []) {
    events.push(
      ...[...childLog.events]
        .sort((left, right) => left.sequence - right.sequence)
        .map((event) => withChildLineage(normalizeRuntimeSessionEvent(event), childLog)),
    );
  }
  return events.sort((left, right) =>
    `${left.ts}\u0000${left.session_id}\u0000${left.sequence}`.localeCompare(
      `${right.ts}\u0000${right.session_id}\u0000${right.sequence}`,
    ),
  );
}

export function normalizeRuntimeSessionEvent(event: RuntimeSessionEvent): NormalizedSessionEvent {
  switch (event.eventType) {
    case "prompt_submitted":
      return baseEvent(event, {
        normalizedEvent: "prompt_started",
        status: "running",
        title: "Prompt started",
        payloadSummary: pickPayload(event.payload, {
          request_id: "requestId",
          role: "role",
        }),
      });
    case "assistant_message":
      return baseEvent(event, {
        normalizedEvent: "runtime_event",
        status: runtimeActionStatus(event.payload, "completed"),
        title: "Assistant message",
        payloadSummary: pickPayload(event.payload, {
          request_id: "requestId",
          role: "role",
        }),
      });
    case "shell_command":
      return baseEvent(event, {
        normalizedEvent: "runtime_event",
        status: runtimeActionStatus(event.payload, "running"),
        title: "Shell command",
        payloadSummary: pickPayload(event.payload, {
          command: "command",
          cwd: "cwd",
          exit_code: "exitCode",
        }),
      });
    case "tool_call":
      return baseEvent(event, {
        normalizedEvent: "runtime_event",
        status: runtimeActionStatus(event.payload, "completed"),
        title: "Tool call",
        payloadSummary: pickPayload(event.payload, {
          tool: "tool",
          name: "name",
        }),
      });
    case "child_task_started":
      return baseEvent(event, {
        normalizedEvent: "child_session_created",
        status: "running",
        title: "Child session created",
        payloadSummary: pickPayload(event.payload, {
          child_session_id: "childSessionId",
          role: "role",
          task_id: "taskId",
        }),
      });
    case "child_task_completed": {
      const canceled = isCanceledPayload(event.payload);
      const failed = readBoolean(event.payload.isError);
      return baseEvent(event, {
        normalizedEvent: "session_status",
        status: canceled ? "canceled" : failed ? "failed" : "completed",
        title: canceled
          ? "Child session canceled"
          : failed
            ? "Child session failed"
            : "Child session completed",
        payloadSummary: pickPayload(event.payload, {
          task_id: "taskId",
        }),
      });
    }
    case "compaction":
      return baseEvent(event, {
        normalizedEvent: "runtime_event",
        status: "completed",
        title: "Compaction recorded",
        payloadSummary: pickPayload(event.payload, {
          summary_artifact_id: "summaryArtifactId",
        }),
      });
  }
}

export function buildLifecycleSessionEvent(
  input: LifecycleSessionEventInput,
): NormalizedSessionEvent {
  const failed = input.phase === "failed" || input.phase === "timeout";
  const completed = input.phase === "completed";
  const skipped = input.phase === "skipped";
  const event =
    completed && input.hook === "start"
      ? "executor_ready"
      : failed || skipped
        ? "session_status"
        : "executor_starting";
  return {
    event_id: `lifecycle:${input.sessionId}:${input.hook}:${input.phase}:${input.sequence}`,
    session_id: input.sessionId,
    sequence: input.sequence,
    ts: input.timestamp,
    event,
    source_event_type: "lifecycle_hook",
    status: skipped ? "skipped" : failed ? "failed" : completed ? "completed" : "running",
    title: `Lifecycle hook ${input.hook} ${input.phase}`,
    payload_summary: { hook: input.hook, phase: input.phase },
  };
}

export function buildSandboxCapabilitySessionEvent(
  input: SandboxCapabilitySessionEventInput,
): NormalizedSessionEvent {
  void input.error;
  const failed = input.phase === "failed" || input.phase === "timeout";
  const completed = input.phase === "completed";
  const skipped =
    input.phase === "unsupported" || input.phase === "skipped" || input.phase === "degraded";
  const event: NormalizedSessionEventName =
    input.phase === "started"
      ? "executor_starting"
      : completed
        ? "executor_ready"
        : "session_status";
  const status: NormalizedSessionEventStatus = failed
    ? "failed"
    : completed
      ? "completed"
      : skipped
        ? "skipped"
        : "running";
  return {
    event_id: `sandbox:${input.sessionId}:${input.capability}:${input.phase}:${input.sequence}`,
    session_id: input.sessionId,
    sequence: input.sequence,
    ts: input.timestamp,
    event,
    source_event_type: "sandbox_capability",
    status,
    title: `Sandbox ${input.capability.replaceAll("_", " ")} ${input.phase}`,
    payload_summary: sanitizeSummary({
      capability: input.capability,
      phase: input.phase,
      boot_mode: input.bootMode,
      provider: input.provider,
      unsupported_policy: input.unsupportedPolicy,
      reason: safeSandboxCapabilityReason(input.reason),
      degraded: input.degraded ?? false,
    }),
  };
}

export function buildArtifactCreatedSessionEvent(
  input: ArtifactCreatedSessionEventInput,
): NormalizedSessionEvent {
  return {
    event_id: `artifact:${input.sessionId}:${input.artifactId}:${input.sequence}`,
    session_id: input.sessionId,
    sequence: input.sequence,
    ts: input.timestamp,
    event: "artifact_created",
    source_event_type: "artifact",
    status: "completed",
    title: "Artifact created",
    payload_summary: sanitizeSummary({
      artifact_id: input.artifactId,
      kind: input.kind,
      label: input.label,
      path: input.path,
      url: input.url,
    }),
  };
}

export function buildSessionStatusEvent(input: SessionStatusEventInput): NormalizedSessionEvent {
  const terminal = new Set<NormalizedSessionEventStatus>([
    "completed",
    "failed",
    "canceled",
    "skipped",
  ]).has(input.status);
  return {
    event_id: `status:${input.sessionId}:${input.status}:${input.sequence}`,
    session_id: input.sessionId,
    sequence: input.sequence,
    ts: input.timestamp,
    event: terminal ? "session_completed" : "session_status",
    source_event_type: "session_status",
    status: input.status,
    title: `Session ${input.status}`,
    payload_summary: sanitizeSummary({ reason: input.reason }),
  };
}

function baseEvent(
  event: RuntimeSessionEvent,
  opts: {
    normalizedEvent: NormalizedSessionEventName;
    status: NormalizedSessionEventStatus;
    title: string;
    payloadSummary: Record<string, NormalizedSessionEventSummaryValue>;
  },
): NormalizedSessionEvent {
  return {
    event_id: event.eventId,
    session_id: event.sessionId,
    sequence: event.sequence,
    ts: event.timestamp,
    event: opts.normalizedEvent,
    source_event_type: event.eventType,
    status: opts.status,
    title: opts.title,
    payload_summary: opts.payloadSummary,
  };
}

function withChildLineage(
  event: NormalizedSessionEvent,
  childLog: RuntimeSessionEventLog,
): NormalizedSessionEvent {
  return {
    ...event,
    payload_summary: {
      ...event.payload_summary,
      ...sanitizeSummary({
        child_session_id: childLog.sessionId,
        parent_session_id: childLog.parentSessionId,
        task_id: childLog.taskId,
        worker_id: childLog.workerId,
      }),
    },
  };
}

function pickPayload(
  payload: Record<string, unknown>,
  mapping: Record<string, string>,
): Record<string, NormalizedSessionEventSummaryValue> {
  const summary: Record<string, NormalizedSessionEventSummaryValue> = {};
  for (const [outputKey, inputKey] of Object.entries(mapping)) {
    const value = payload[inputKey];
    if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
      summary[outputKey] = value;
    }
  }
  return summary;
}

function sanitizeSummary(
  value: Record<string, unknown>,
): Record<string, NormalizedSessionEventSummaryValue> {
  const summary: Record<string, NormalizedSessionEventSummaryValue> = {};
  for (const [key, item] of Object.entries(value)) {
    if (typeof item === "string" || typeof item === "number" || typeof item === "boolean") {
      summary[key] = item;
    }
  }
  return summary;
}

function safeSandboxCapabilityReason(reason: string | undefined): string | undefined {
  return reason && SAFE_SANDBOX_CAPABILITY_REASONS.has(reason) ? reason : undefined;
}

function runtimeActionStatus(
  payload: Record<string, unknown>,
  defaultStatus: NormalizedSessionEventStatus,
): NormalizedSessionEventStatus {
  if (isCanceledPayload(payload)) {
    return "canceled";
  }
  if (hasFailurePayload(payload)) {
    return "failed";
  }
  if (hasCompletedPayload(payload)) {
    return "completed";
  }
  return defaultStatus;
}

function isCanceledPayload(payload: Record<string, unknown>): boolean {
  return [readPhase(payload), readStatus(payload)].some(
    (value) => value === "canceled" || value === "cancelled",
  );
}

function hasFailurePayload(payload: Record<string, unknown>): boolean {
  const exitCode = readNumber(payload.exitCode);
  return (
    readBoolean(payload.isError) ||
    (exitCode !== null && exitCode !== 0) ||
    readNonEmptyString(payload.error) !== "" ||
    new Set(["error", "failed", "failure", "timeout", "timed_out"]).has(readPhase(payload))
  );
}

function hasCompletedPayload(payload: Record<string, unknown>): boolean {
  const exitCode = readNumber(payload.exitCode);
  return (
    exitCode === 0 ||
    new Set(["completed", "complete", "success", "succeeded"]).has(readPhase(payload))
  );
}

function readPhase(payload: Record<string, unknown>): string {
  return readNonEmptyString(payload.phase).toLowerCase().replace(/-/g, "_");
}

function readStatus(payload: Record<string, unknown>): string {
  return readNonEmptyString(payload.status).toLowerCase().replace(/-/g, "_");
}

function readNonEmptyString(value: unknown): string {
  return typeof value === "string" && value.trim() ? value.trim() : "";
}

function readNumber(value: unknown): number | null {
  return typeof value === "number" ? value : null;
}

function readBoolean(value: unknown): boolean {
  return value === true;
}
