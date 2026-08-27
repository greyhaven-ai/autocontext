import { Buffer } from "node:buffer";

export const EVENT_TRACE_VERSION = 1;

export type EventTracePhase = "start" | "complete" | "instant";
export type EventTraceKind =
  | "run"
  | "generation"
  | "agent"
  | "evaluation"
  | "decision"
  | "storage"
  | "runtime"
  | "event";

export interface EventTraceSpan {
  version: 1;
  trace_id: string;
  span_id: string;
  parent_span_id?: string;
  name: string;
  kind: EventTraceKind;
  phase: EventTracePhase;
  started_at: string;
  ended_at?: string;
  duration_ms?: number;
  payload_bytes: number;
}

interface SpanDescriptor {
  runId: string;
  generation?: number;
  name: string;
  kind: EventTraceKind;
  phase: EventTracePhase;
  key: string;
}

export class EventTraceTracker {
  readonly #starts = new Map<string, string>();

  trace(
    event: string,
    payload: Record<string, unknown>,
    timestamp: string,
    sequence: number,
  ): EventTraceSpan {
    const descriptor = describeSpan(event, payload, sequence);
    const traceId = `autocontext:${segment(descriptor.runId)}`;
    const spanId = `${traceId}:${descriptor.key}`;
    const parentSpanId = parentFor(traceId, descriptor);
    const measuredDuration = readDuration(payload);
    let startedAt = timestamp;
    let endedAt: string | undefined;
    let durationMs: number | undefined;

    if (descriptor.phase === "start") {
      this.#starts.set(spanId, timestamp);
    } else if (descriptor.phase === "complete") {
      const pairedStartedAt = this.#starts.get(spanId);
      startedAt = pairedStartedAt ?? subtractDuration(timestamp, measuredDuration);
      this.#starts.delete(spanId);
      endedAt = timestamp;
      durationMs = pairedStartedAt
        ? elapsedMilliseconds(startedAt, timestamp)
        : measuredDuration ?? elapsedMilliseconds(startedAt, timestamp);
    } else {
      endedAt = timestamp;
      durationMs = 0;
    }

    return {
      version: EVENT_TRACE_VERSION,
      trace_id: traceId,
      span_id: spanId,
      ...(parentSpanId ? { parent_span_id: parentSpanId } : {}),
      name: descriptor.name,
      kind: descriptor.kind,
      phase: descriptor.phase,
      started_at: startedAt,
      ...(endedAt ? { ended_at: endedAt } : {}),
      ...(durationMs === undefined ? {} : { duration_ms: durationMs }),
      payload_bytes: Buffer.byteLength(JSON.stringify(payload), "utf-8"),
    };
  }
}

export function readEventTraceSpan(value: unknown): EventTraceSpan | undefined {
  if (!isRecord(value) || value.version !== EVENT_TRACE_VERSION) return undefined;
  const traceId = readString(value.trace_id);
  const spanId = readString(value.span_id);
  const name = readString(value.name);
  const kind = readString(value.kind) as EventTraceKind;
  const phase = readString(value.phase) as EventTracePhase;
  const startedAt = readString(value.started_at);
  const payloadBytes = readNumber(value.payload_bytes);
  if (
    !traceId || !spanId || !name || !startedAt || payloadBytes === undefined
    || !TRACE_KINDS.has(kind) || !TRACE_PHASES.has(phase)
  ) return undefined;
  const parentSpanId = readString(value.parent_span_id);
  const endedAt = readString(value.ended_at);
  const durationMs = readNumber(value.duration_ms);
  return {
    version: EVENT_TRACE_VERSION,
    trace_id: traceId,
    span_id: spanId,
    ...(parentSpanId ? { parent_span_id: parentSpanId } : {}),
    name,
    kind,
    phase,
    started_at: startedAt,
    ...(endedAt ? { ended_at: endedAt } : {}),
    ...(durationMs === undefined ? {} : { duration_ms: durationMs }),
    payload_bytes: payloadBytes,
  };
}

const TRACE_KINDS = new Set<EventTraceKind>([
  "run", "generation", "agent", "evaluation", "decision", "storage", "runtime", "event",
]);
const TRACE_PHASES = new Set<EventTracePhase>(["start", "complete", "instant"]);

function describeSpan(
  outerEvent: string,
  outerPayload: Record<string, unknown>,
  sequence: number,
): SpanDescriptor {
  const runtimeEvent = outerEvent === "runtime_session_event"
    ? readRecord(readRecord(outerPayload.event).payload)
    : outerPayload;
  const event = outerEvent === "runtime_session_event"
    ? readString(readRecord(outerPayload.event).event_type) || outerEvent
    : outerEvent;
  const runId = readString(runtimeEvent.run_id)
    || readString(runtimeEvent.runId)
    || readString(outerPayload.session_id)
    || "unscoped";
  const generation = readInteger(runtimeEvent.generation);
  const attempt = readInteger(runtimeEvent.attempt) ?? readInteger(runtimeEvent.retry_attempt);
  const role = readString(runtimeEvent.role).toLowerCase();
  const scope = generation === undefined ? "" : `:generation:${generation}`;
  const attemptScope = attempt === undefined ? "" : `:attempt:${attempt}`;

  if (["run_started", "run_completed", "run_failed", "run_stopped"].includes(event)) {
    return descriptor(runId, generation, "run", "run", pairedPhase(event), "run");
  }
  if (["generation_started", "generation_completed", "generation_failed"].includes(event)) {
    return descriptor(
      runId,
      generation,
      "generation",
      "generation",
      pairedPhase(event),
      `generation:${generation ?? "unknown"}`,
    );
  }
  if (["role_started", "role_completed", "role_failed"].includes(event)) {
    const roleName = role || "unknown";
    return descriptor(
      runId,
      generation,
      `role.${roleName}`,
      "agent",
      pairedPhase(event),
      `generation:${generation ?? "unknown"}:role:${segment(roleName)}${attemptScope}`,
    );
  }
  if (["tournament_started", "tournament_completed"].includes(event)) {
    return descriptor(
      runId,
      generation,
      "tournament",
      "evaluation",
      pairedPhase(event),
      `generation:${generation ?? "unknown"}:tournament${attemptScope}`,
    );
  }
  if (["persistence_started", "persistence_completed"].includes(event)) {
    return descriptor(
      runId,
      generation,
      "persistence",
      "storage",
      pairedPhase(event),
      `generation:${generation ?? "unknown"}:persistence`,
    );
  }
  if (["curator_started", "curator_completed"].includes(event)) {
    return descriptor(
      runId,
      generation,
      "curation",
      "storage",
      pairedPhase(event),
      `generation:${generation ?? "unknown"}:curation`,
    );
  }
  if (event === "gate_decided") {
    return descriptor(
      runId,
      generation,
      "gate",
      "decision",
      "instant",
      `generation:${generation ?? "unknown"}:gate${attemptScope}:event:${sequence}`,
    );
  }
  if (outerEvent === "runtime_session_event") {
    return descriptor(
      runId,
      generation,
      event,
      "runtime",
      phaseFromPayload(runtimeEvent),
      `runtime:${segment(event)}:event:${sequence}`,
    );
  }
  return descriptor(
    runId,
    generation,
    event,
    "event",
    "instant",
    `${scope.slice(1) || "run"}:event:${segment(event)}:${sequence}`,
  );
}

function descriptor(
  runId: string,
  generation: number | undefined,
  name: string,
  kind: EventTraceKind,
  phase: EventTracePhase,
  key: string,
): SpanDescriptor {
  return { runId, ...(generation === undefined ? {} : { generation }), name, kind, phase, key };
}

function parentFor(traceId: string, descriptor: SpanDescriptor): string | undefined {
  if (descriptor.kind === "run") return undefined;
  if (descriptor.kind === "generation") return `${traceId}:run`;
  if (descriptor.generation !== undefined) return `${traceId}:generation:${descriptor.generation}`;
  return `${traceId}:run`;
}

function pairedPhase(event: string): EventTracePhase {
  return event.endsWith("_started") ? "start" : "complete";
}

function phaseFromPayload(payload: Record<string, unknown>): EventTracePhase {
  const status = readString(payload.status).toLowerCase();
  if (["started", "running"].includes(status)) return "start";
  if (["completed", "failed", "error", "stopped"].includes(status)) return "complete";
  return "instant";
}

function readDuration(payload: Record<string, unknown>): number | undefined {
  const value = payload.duration_ms ?? payload.durationMs ?? payload.latency_ms;
  const duration = readNumber(value);
  return duration === undefined ? undefined : Math.max(0, Math.round(duration));
}

function subtractDuration(timestamp: string, durationMs?: number): string {
  if (durationMs === undefined) return timestamp;
  const endedAt = Date.parse(timestamp);
  return Number.isFinite(endedAt) ? new Date(endedAt - durationMs).toISOString() : timestamp;
}

function elapsedMilliseconds(startedAt: string, endedAt: string): number {
  const elapsed = Date.parse(endedAt) - Date.parse(startedAt);
  return Number.isFinite(elapsed) ? Math.max(0, elapsed) : 0;
}

function segment(value: string): string {
  return encodeURIComponent(value.trim() || "unknown");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readRecord(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {};
}

function readString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function readNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function readInteger(value: unknown): number | undefined {
  return typeof value === "number" && Number.isInteger(value) ? value : undefined;
}
