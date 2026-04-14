import type { RunTrace } from "../analytics/run-trace.js";
import {
  SCHEMA_VERSION,
  type PublicTrace,
  type TraceMessage,
} from "./public-schema-contracts.js";

export const SYSTEM_TRACE_EVENTS = new Set([
  "generation_started",
  "generation_completed",
  "tournament_started",
  "tournament_completed",
  "gate_decided",
  "run_started",
  "run_completed",
]);

export const ASSISTANT_TRACE_ROLES = new Set([
  "competitor",
  "analyst",
  "coach",
  "architect",
  "curator",
  "translator",
]);

export function mapRunTraceEventToPublicMessage(event: RunTrace["events"][number]): TraceMessage {
  const actorRole = event.actor.actorName || event.actor.actorId;
  let role: TraceMessage["role"];

  if (SYSTEM_TRACE_EVENTS.has(event.eventType)) {
    role = "system";
  } else if (ASSISTANT_TRACE_ROLES.has(actorRole) || event.eventType === "role_completed") {
    role = "assistant";
  } else if (event.eventType.includes("user") || actorRole === "user") {
    role = "user";
  } else {
    role = "system";
  }

  return {
    role,
    content: String(event.payload.output ?? event.payload.description ?? event.eventType),
    timestamp: event.timestamp,
    metadata: {
      eventType: event.eventType,
      internalRole: actorRole,
      actor: event.actor.toDict(),
      ...event.payload,
    },
  };
}

export function exportRunTraceToPublicTrace(
  trace: RunTrace,
  opts: {
    sourceHarness: string;
    model?: string;
    provider?: string;
  },
): PublicTrace {
  const messages = trace.events.map(mapRunTraceEventToPublicMessage);

  if (messages.length === 0) {
    messages.push({
      role: "system",
      content: `Trace ${trace.runId} for ${trace.scenarioType}`,
      timestamp: trace.createdAt,
    });
  }

  return {
    schemaVersion: SCHEMA_VERSION,
    traceId: `trace_${trace.runId}`,
    sessionId: trace.runId,
    sourceHarness: opts.sourceHarness,
    collectedAt: trace.createdAt,
    messages,
    metadata: {
      model: opts.model,
      provider: opts.provider,
      scenarioType: trace.scenarioType,
      eventCount: trace.events.length,
    },
  };
}
