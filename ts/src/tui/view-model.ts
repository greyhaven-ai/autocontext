import {
  AGENT_PROGRESS_NOTE_EVENT_NAME,
  AGENT_TASK_PLAN_EVENT_NAME,
  PROTOCOL_VERSION,
  TRANSCRIPT_PROTOCOL_VERSION,
  type AgentProgressNotePayload,
  type AgentTaskPlanPayload,
  type ServerMessage,
} from "../server/protocol.js";
import { sanitizeAgentProgressNotePayload } from "../loop/agent-progress-note.js";
import { sanitizeAgentTaskPlanPayload } from "../loop/agent-task-plan.js";

export type TuiConnectionStatus =
  | "connecting"
  | "connected"
  | "reconnecting"
  | "disconnected"
  | "failed";

export interface TuiTranscriptRow {
  readonly id: string;
  readonly sequence?: number;
  readonly occurredAt?: string | number;
  readonly event: string;
  readonly kind:
    | "lifecycle"
    | "message"
    | "progress"
    | "plan"
    | "decision"
    | "receipt"
    | "error"
    | "unknown";
  readonly tone: "normal" | "muted" | "success" | "warning" | "error";
  readonly title: string;
  readonly detail?: string;
  readonly activity: {
    readonly family: "run" | "runtime";
    readonly focus: "run" | "runtime" | "prompt" | "command" | "child";
    readonly hasError: boolean;
  };
  readonly commandId?: string;
  readonly runId?: string;
}

export interface TuiScenarioView {
  readonly name: string;
  readonly description: string;
  readonly origin: "builtin" | "custom" | "unknown";
  readonly available: boolean;
}

export interface TuiRoutingView {
  readonly provider: string;
  readonly model?: string;
  readonly hostingClass?: string;
  readonly capabilityTier?: string;
  readonly roles: Readonly<Record<string, {
    readonly provider: string;
    readonly model: string;
    readonly capabilityTier?: string;
  }>>;
}

export interface TuiRunView {
  readonly active: boolean;
  readonly paused: boolean;
  readonly clientRunId: string | null;
  readonly runId: string | null;
  readonly scenario: string | null;
  readonly generation: number | null;
  readonly phase: string | null;
  readonly outcome: "completed" | "failed" | "stopped" | null;
}

export interface TuiCommandReceipt {
  readonly commandId: string;
  readonly action: string;
  readonly status: "acknowledged" | "completed" | "failed";
  readonly detail?: string;
  readonly sequence?: number;
}

export interface TuiPendingDecision {
  readonly id: string;
  readonly kind: "playbook_approval" | "gate" | "approval";
  readonly title: string;
  readonly runId?: string;
  readonly scenario?: string;
  readonly sequence?: number;
}

export interface TuiViewModel {
  readonly endpoint: string;
  readonly connection: {
    readonly status: TuiConnectionStatus;
    readonly attempt: number;
    readonly error?: string;
  };
  readonly capabilities: readonly string[];
  readonly protocolCompatible: boolean;
  readonly scenarios: readonly TuiScenarioView[];
  readonly executors: readonly string[];
  readonly routing: TuiRoutingView;
  readonly run: TuiRunView;
  readonly transcript: readonly TuiTranscriptRow[];
  readonly taskPlan: AgentTaskPlanPayload | null;
  readonly progressNotes: readonly (AgentProgressNotePayload & { sequence?: number })[];
  readonly pendingDecisions: readonly TuiPendingDecision[];
  readonly receipts: Readonly<Record<string, TuiCommandReceipt>>;
  readonly seenEventIds: Readonly<Record<string, true>>;
  readonly retiredClientRunIds: readonly string[];
  readonly lastSequence: number;
  readonly busyCommandId: string | null;
}

export type TuiViewModelInput =
  | { readonly kind: "connection"; readonly status: TuiConnectionStatus; readonly attempt?: number; readonly error?: string }
  | { readonly kind: "busy"; readonly commandId: string | null }
  | { readonly kind: "decision_resolved"; readonly scenario: string }
  | { readonly kind: "message"; readonly message: ServerMessage };

export const TUI_HIDDEN_EVENT_NAMES = new Set<string>();
export const MAX_TUI_TRANSCRIPT_ROWS = 2_000;
export const MAX_TUI_SEEN_EVENT_IDS = MAX_TUI_TRANSCRIPT_ROWS;

const LIFECYCLE_LABELS: Readonly<Record<string, string>> = {
  run_started: "Run started",
  generation_started: "Generation started",
  agents_started: "Agents started",
  role_completed: "Role completed",
  tournament_started: "Tournament started",
  match_completed: "Match completed",
  tournament_completed: "Tournament completed",
  gate_decided: "Gate decided",
  generation_completed: "Generation completed",
  generation_timing: "Generation timing",
  curator_started: "Curator started",
  curator_completed: "Curator completed",
  playbook_update_skipped: "Playbook update skipped",
  run_completed: "Run completed",
  run_failed: "Run failed",
  run_stopped: "Run stopped",
  dead_end_recorded: "Dead end recorded",
  fresh_start: "Fresh start queued",
  runtime_session_event: "Runtime session event",
  monitor_started: "Monitor started",
  monitor_stopped: "Monitor stopped",
  approval_requested: "Approval requested",
  approval_decided: "Approval decided",
};

export function createInitialTuiViewModel(endpoint: string): TuiViewModel {
  return {
    endpoint,
    connection: { status: "connecting", attempt: 0 },
    capabilities: [],
    protocolCompatible: false,
    scenarios: [],
    executors: [],
    routing: { provider: "unknown", roles: {} },
    run: {
      active: false,
      paused: false,
      clientRunId: null,
      runId: null,
      scenario: null,
      generation: null,
      phase: null,
      outcome: null,
    },
    transcript: [],
    taskPlan: null,
    progressNotes: [],
    pendingDecisions: [],
    receipts: {},
    seenEventIds: {},
    retiredClientRunIds: [],
    lastSequence: 0,
    busyCommandId: null,
  };
}

export function reduceTuiViewModel(
  state: TuiViewModel,
  input: TuiViewModelInput,
): TuiViewModel {
  if (input.kind === "connection") {
    return {
      ...state,
      ...(input.status === "connected" || input.status === "reconnecting"
        ? { protocolCompatible: false, capabilities: [] }
        : {}),
      connection: {
        status: input.status,
        attempt: input.attempt ?? state.connection.attempt,
        ...(input.error ? { error: input.error } : {}),
      },
    };
  }
  if (input.kind === "busy") return { ...state, busyCommandId: input.commandId };
  if (input.kind === "decision_resolved") {
    return {
      ...state,
      pendingDecisions: state.pendingDecisions.filter(
        (decision) => decision.scenario !== input.scenario,
      ),
    };
  }

  const message = input.message;
  const messageClientRunId = readMessageClientRunId(message);
  const eventId = readMessageString(message, "event_id");
  const sequence = readMessageNumber(message, "sequence");
  if (
    messageClientRunId &&
    state.retiredClientRunIds.includes(messageClientRunId)
  ) {
    // A socket can drain delayed frames after a run transition. Never allow a
    // scope that this reducer already left to become current again.
    return state;
  }
  if (
    messageClientRunId &&
    state.run.clientRunId &&
    messageClientRunId !== state.run.clientRunId &&
    message.type !== "run_accepted" &&
    !(message.type === "state" && sequence === undefined)
  ) return state;
  const scopedState = messageClientRunId && messageClientRunId !== state.run.clientRunId
    ? resetRunScope(state, messageClientRunId, readMessageRunId(message))
    : state;
  const outOfOrder = sequence !== undefined && sequence < scopedState.lastSequence;
  if (eventId && scopedState.seenEventIds[eventId]) return scopedState;
  const seenEventIds = rememberEventId(scopedState.seenEventIds, eventId);
  const lastSequence = Math.max(scopedState.lastSequence, sequence ?? 0);
  const base = { ...scopedState, seenEventIds, lastSequence };

  switch (message.type) {
    case "hello": {
      const compatible =
        message.protocol_version === PROTOCOL_VERSION &&
        message.transcript_protocol_version === TRANSCRIPT_PROTOCOL_VERSION;
      return {
        ...base,
        protocolCompatible: compatible,
        capabilities: [...new Set(message.capabilities ?? [])].sort(),
        connection: compatible
          ? { status: "connected", attempt: state.connection.attempt }
          : {
              status: "failed",
              attempt: state.connection.attempt,
              error: "Interactive protocol version is unsupported",
            },
      };
    }
    case "environments":
      return {
        ...base,
        scenarios: message.scenarios.map((scenario) => ({
          name: scenario.name,
          description: scenario.description,
          origin: scenario.origin ?? "unknown",
          available: scenario.available ?? true,
        })),
        executors: message.executors
          .filter((executor) => executor.available)
          .map((executor) => executor.mode),
        routing: message.routing_context ?? {
          ...state.routing,
          provider: message.agent_provider,
        },
      };
    case "auth_status":
      return {
        ...base,
        routing: {
          ...state.routing,
          provider: message.provider,
          ...(message.model ? { model: message.model } : {}),
        },
      };
    case "state":
      return {
        ...base,
        run: outOfOrder ? state.run : {
          ...state.run,
          active: message.active ?? state.run.active,
          paused: message.paused,
          clientRunId: message.client_run_id ?? state.run.clientRunId,
          runId: message.run_id ?? state.run.runId,
          scenario: message.scenario ?? state.run.scenario,
          generation: message.generation ?? state.run.generation,
          phase: message.phase ?? state.run.phase,
        },
      };
    case "run_accepted":
      return appendRow({
        ...base,
        run: outOfOrder ? state.run : {
          active: true,
          paused: false,
          clientRunId: message.client_run_id ?? state.run.clientRunId,
          runId: message.run_id,
          scenario: message.scenario,
          generation: 0,
          phase: "accepted",
          outcome: null,
        },
      }, rowForMessage(message, "lifecycle", "Run accepted", `${message.scenario} · ${message.generations} iterations`));
    case "chat_response":
      return appendRow(
        withReceipt(base, message.command_id, "chat_agent", "completed", undefined, message.sequence),
        rowForMessage(message, "message", `[${message.role}]`, message.text),
      );
    case "ack":
      return appendRow(
        withReceipt(base, message.command_id, message.action, "acknowledged", message.decision ?? undefined, message.sequence),
        rowForMessage(message, "receipt", `${message.action} acknowledged`, message.decision ?? undefined),
      );
    case "error":
      return appendRow(
        withReceipt(base, message.command_id, "command", "failed", message.message, message.sequence),
        rowForMessage(message, "error", "Command failed", message.message, "error"),
      );
    case "monitor_alert":
      return appendRow(
        base,
        rowForMessage(message, "decision", `Monitor: ${message.condition_name}`, message.detail, "warning"),
      );
    case "event":
      return reduceEvent(base, message, outOfOrder);
    case "scenario_generating":
      return appendRow(base, rowForMessage(message, "lifecycle", "Generating scenario", message.name));
    case "scenario_preview":
      return appendRow(base, rowForMessage(message, "decision", "Scenario ready for review", message.display_name, "warning"));
    case "scenario_ready":
      return appendRow(base, rowForMessage(message, "lifecycle", "Scenario saved", message.name, "success"));
    case "scenario_error":
      return appendRow(base, rowForMessage(message, "error", `Scenario ${message.stage} failed`, message.message, "error"));
    case "mission_progress":
      return appendRow(base, rowForMessage(message, "lifecycle", `Mission ${message.status}`, message.latestStep));
  }
}

function reduceEvent(
  state: TuiViewModel,
  message: Extract<ServerMessage, { type: "event" }>,
  outOfOrder: boolean,
): TuiViewModel {
  if (message.event === AGENT_TASK_PLAN_EVENT_NAME) {
    const parsed = sanitizeAgentTaskPlanPayload(message.payload);
    if (!parsed) return appendUnknownEvent(state, message);
    const current = state.taskPlan;
    const isNewer = !current || parsed.plan_revision > current.plan_revision ||
      (parsed.plan_revision === current.plan_revision && parsed.version >= current.version);
    const next = isNewer ? { ...state, taskPlan: parsed } : state;
    return appendRow(next, rowForMessage(
      message,
      "plan",
      `Plan ${parsed.update_kind} · revision ${parsed.plan_revision}`,
      parsed.summary,
    ));
  }
  if (message.event === AGENT_PROGRESS_NOTE_EVENT_NAME) {
    const parsed = sanitizeAgentProgressNotePayload(message.payload);
    if (!parsed) return appendUnknownEvent(state, message);
    const note = { ...parsed, ...(message.sequence === undefined ? {} : { sequence: message.sequence }) };
    const progressNotes = [...state.progressNotes, note]
      .sort((a, b) => (a.sequence ?? 0) - (b.sequence ?? 0))
      .slice(-100);
    return appendRow(
      { ...state, progressNotes },
      rowForMessage(message, "progress", `Progress · ${parsed.kind}`, parsed.text),
    );
  }
  if (message.event === "action_detail") {
    const name = firstString(
      message.payload.name,
      message.payload.action_name,
      message.payload.tool_name,
      message.payload.tool,
      message.payload.kind,
      message.payload.activity_kind,
    ) ?? "activity";
    return appendRow(
      state,
      rowForMessage(
        message,
        "progress",
        `Action · ${name}`,
        actionMetadataDetail(message.payload),
      ),
    );
  }
  if (TUI_HIDDEN_EVENT_NAMES.has(message.event)) return state;

  if (message.event === "playbook_pending") {
    const scenario = typeof message.payload.scenario === "string"
      ? message.payload.scenario
      : undefined;
    const decision: TuiPendingDecision = {
      id: `playbook:${message.run_id ?? "run"}:${message.sequence ?? state.lastSequence}`,
      kind: "playbook_approval",
      title: "Playbook update needs approval",
      ...(message.run_id ? { runId: message.run_id } : {}),
      ...(scenario ? { scenario } : {}),
      ...(message.sequence === undefined ? {} : { sequence: message.sequence }),
    };
    return appendRow(
      { ...state, pendingDecisions: [...state.pendingDecisions, decision] },
      rowForMessage(
        message,
        "decision",
        decision.title,
        scenario
          ? `Use /approve ${scenario} confirm or /reject ${scenario} confirm.`
          : "Inspect the pending playbook, then use /approve <scenario> confirm or /reject <scenario> confirm.",
        "warning",
      ),
    );
  }

  const label = LIFECYCLE_LABELS[message.event];
  if (!label) return appendUnknownEvent(state, message);
  const terminal = terminalOutcome(message.event);
  const next = terminal && !outOfOrder
    ? {
        ...state,
        run: {
          ...state.run,
          active: false,
          paused: false,
          phase: terminal,
          outcome: terminal,
        },
      }
    : state;
  const detail = lifecycleDetail(message.event, message.payload);
  return appendRow(
    next,
    rowForMessage(
      message,
      "lifecycle",
      label,
      detail,
      terminal === "failed" ? "error" : terminal ? "success" : "normal",
    ),
  );
}

function appendUnknownEvent(
  state: TuiViewModel,
  message: Extract<ServerMessage, { type: "event" }>,
): TuiViewModel {
  return appendRow(
    state,
    rowForMessage(message, "unknown", `Event: ${message.event}`, "No safe renderer is registered for this event version.", "muted"),
  );
}

function appendRow(state: TuiViewModel, row: TuiTranscriptRow): TuiViewModel {
  const transcript = [...state.transcript];
  if (row.sequence === undefined) {
    transcript.push(row);
  } else {
    let low = 0;
    let high = transcript.length;
    while (low < high) {
      const middle = Math.floor((low + high) / 2);
      const middleSequence = transcript[middle]!.sequence;
      if (middleSequence !== undefined && middleSequence <= row.sequence) {
        low = middle + 1;
      } else {
        high = middle;
      }
    }
    transcript.splice(low, 0, row);
  }
  if (transcript.length > MAX_TUI_TRANSCRIPT_ROWS) {
    transcript.splice(0, transcript.length - MAX_TUI_TRANSCRIPT_ROWS);
  }
  return { ...state, transcript };
}

function resetRunScope(
  state: TuiViewModel,
  clientRunId: string,
  runId: string | null,
): TuiViewModel {
  const retiredClientRunIds = state.run.clientRunId && state.run.clientRunId !== clientRunId
    ? [...new Set([...state.retiredClientRunIds, state.run.clientRunId])].slice(-100)
    : state.retiredClientRunIds;
  return {
    ...state,
    run: {
      active: false,
      paused: false,
      clientRunId,
      runId,
      scenario: null,
      generation: null,
      phase: null,
      outcome: null,
    },
    transcript: [],
    taskPlan: null,
    progressNotes: [],
    pendingDecisions: [],
    receipts: {},
    seenEventIds: {},
    retiredClientRunIds,
    lastSequence: 0,
  };
}

function rememberEventId(
  seenEventIds: Readonly<Record<string, true>>,
  eventId: string | undefined,
): Readonly<Record<string, true>> {
  if (!eventId) return seenEventIds;
  const retainedIds = Object.keys(seenEventIds);
  const next: Record<string, true> = {};
  const firstRetainedIndex = Math.max(0, retainedIds.length - MAX_TUI_SEEN_EVENT_IDS + 1);
  for (let index = firstRetainedIndex; index < retainedIds.length; index += 1) {
    next[retainedIds[index]!] = true;
  }
  next[eventId] = true;
  return next;
}

function withReceipt(
  state: TuiViewModel,
  commandId: string | undefined,
  action: string,
  status: TuiCommandReceipt["status"],
  detail?: string,
  sequence?: number,
): TuiViewModel {
  if (!commandId) return state;
  const current = state.receipts[commandId];
  if (
    current?.sequence !== undefined &&
    sequence !== undefined &&
    sequence < current.sequence
  ) return state;
  return {
    ...state,
    receipts: {
      ...state.receipts,
      [commandId]: {
        commandId,
        action,
        status,
        ...(detail ? { detail } : {}),
        ...(sequence === undefined ? {} : { sequence }),
      },
    },
  };
}

function rowForMessage(
  message: ServerMessage,
  kind: TuiTranscriptRow["kind"],
  title: string,
  detail?: string,
  tone: TuiTranscriptRow["tone"] = kind === "error" ? "error" : "normal",
): TuiTranscriptRow {
  const event = message.type === "event" ? message.event : message.type;
  const eventId = readMessageString(message, "event_id");
  const sequence = readMessageNumber(message, "sequence");
  const occurredAt = readMessageStringOrNumber(message, "occurred_at");
  return {
    id: eventId ?? `${event}:${sequence ?? "live"}:${title}`,
    event,
    kind,
    tone,
    title,
    activity: transcriptActivity(message, tone),
    ...(detail ? { detail } : {}),
    ...(sequence === undefined ? {} : { sequence }),
    ...(occurredAt === undefined ? {} : { occurredAt }),
    ...("command_id" in message && message.command_id ? { commandId: message.command_id } : {}),
    ...("run_id" in message && message.run_id ? { runId: message.run_id } : {}),
  };
}

function transcriptActivity(
  message: ServerMessage,
  tone: TuiTranscriptRow["tone"],
): TuiTranscriptRow["activity"] {
  if (message.type !== "event" || message.event !== "runtime_session_event") {
    return {
      family: "run",
      focus: "run",
      hasError:
        tone === "error" ||
        (message.type === "event" &&
          (message.event === "run_failed" || message.event === "playbook_update_skipped")),
    };
  }
  const event = readRecord(message.payload.event);
  const eventType = readString(event.event_type) ?? readString(event.eventType) ?? "";
  const payload = readRecord(event.payload);
  const focus = runtimeEventFocus(eventType);
  return {
    family: "runtime",
    focus,
    hasError:
      tone === "error" ||
      Boolean(payload.error) ||
      payload.isError === true ||
      payload.is_error === true ||
      payload.has_error === true ||
      eventType === "run_failed",
  };
}

function runtimeEventFocus(
  eventType: string,
): TuiTranscriptRow["activity"]["focus"] {
  if (eventType === "prompt_submitted" || eventType === "assistant_message") return "prompt";
  if (eventType === "shell_command" || eventType === "tool_call") return "command";
  if (eventType === "child_task_started" || eventType === "child_task_completed") return "child";
  return "runtime";
}

function readRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function readString(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function readMessageString(
  message: ServerMessage,
  key: "event_id",
): string | undefined {
  if (!(key in message)) return undefined;
  const value = message[key];
  return typeof value === "string" ? value : undefined;
}

function readMessageNumber(
  message: ServerMessage,
  key: "sequence",
): number | undefined {
  if (!(key in message)) return undefined;
  const value = message[key];
  return typeof value === "number" ? value : undefined;
}

function readMessageStringOrNumber(
  message: ServerMessage,
  key: "occurred_at",
): string | number | undefined {
  if (!(key in message)) return undefined;
  const value = message[key];
  return typeof value === "string" || typeof value === "number" ? value : undefined;
}

function readMessageClientRunId(message: ServerMessage): string | null {
  if (!("client_run_id" in message)) return null;
  return typeof message.client_run_id === "string" ? message.client_run_id : null;
}

function readMessageRunId(message: ServerMessage): string | null {
  if (!("run_id" in message)) return null;
  return typeof message.run_id === "string" ? message.run_id : null;
}

function terminalOutcome(event: string): TuiRunView["outcome"] {
  if (event === "run_completed") return "completed";
  if (event === "run_failed") return "failed";
  if (event === "run_stopped") return "stopped";
  return null;
}

function lifecycleDetail(event: string, payload: Record<string, unknown>): string | undefined {
  const fieldsByEvent: Readonly<Record<string, readonly string[]>> = {
    run_started: ["scenario", "target_generations"],
    generation_started: ["generation"],
    agents_started: ["generation", "roles"],
    role_completed: ["role", "latency_ms"],
    tournament_started: ["generation", "matches"],
    match_completed: ["generation", "match_index", "score"],
    tournament_completed: ["mean_score", "best_score"],
    gate_decided: ["decision", "delta", "reason"],
    generation_completed: ["generation", "best_score"],
    generation_timing: ["generation", "elapsed_seconds"],
    curator_started: ["generation"],
    curator_completed: ["generation", "decision"],
    playbook_update_skipped: ["reason", "guard_reason"],
    run_completed: ["completed_generations", "best_score"],
    run_failed: ["error"],
    run_stopped: ["reason", "completed_generations", "best_score"],
    dead_end_recorded: ["generation", "reason"],
    fresh_start: ["generation", "reason"],
    runtime_session_event: ["session_id"],
  };
  const fields = fieldsByEvent[event] ?? [];
  const values = fields.flatMap((field) => {
    const value = payload[field];
    return value === undefined || value === null || value === "" ? [] : [`${field}=${String(value)}`];
  });
  return values.length ? values.join(" · ") : undefined;
}

function actionMetadataDetail(payload: Record<string, unknown>): string | undefined {
  const metadata: Array<[string, unknown]> = [
    ["action_id", payload.action_id ?? payload.id],
    ["role", payload.role],
    ["status", payload.status],
    ["kind", payload.kind ?? payload.activity_kind],
    ["generation", payload.generation],
    ["tool", payload.tool_name ?? payload.tool],
    ["duration_ms", payload.duration_ms],
    ["artifacts", Array.isArray(payload.artifacts) ? payload.artifacts.length : undefined],
  ];
  const values = metadata.flatMap(([key, value]) =>
    typeof value === "string" || typeof value === "number" || typeof value === "boolean"
      ? [`${key}=${String(value)}`]
      : []);
  return values.length ? values.join(" · ") : undefined;
}

function firstString(...values: unknown[]): string | undefined {
  return values.find((value): value is string => typeof value === "string" && value.length > 0);
}
