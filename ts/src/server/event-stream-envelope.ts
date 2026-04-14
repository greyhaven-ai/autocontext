import type { ServerMessage } from "./protocol.js";

export interface EventStreamEnvelope<TPayload> {
  channel: "generation" | "mission";
  event: string;
  payload: TPayload;
  ts: string;
  v: 1;
}

export function buildEventStreamEnvelope<TPayload>(opts: {
  channel: EventStreamEnvelope<TPayload>["channel"];
  event: string;
  payload: TPayload;
  timestamp?: string;
}): EventStreamEnvelope<TPayload> {
  return {
    channel: opts.channel,
    event: opts.event,
    payload: opts.payload,
    ts: opts.timestamp ?? new Date().toISOString(),
    v: 1,
  };
}

export function buildGenerationEventEnvelope(
  event: string,
  payload: Record<string, unknown>,
  timestamp?: string,
): EventStreamEnvelope<Record<string, unknown>> {
  return buildEventStreamEnvelope({
    channel: "generation",
    event,
    payload,
    timestamp,
  });
}

export function buildMissionProgressEventEnvelope(
  payload: Extract<ServerMessage, { type: "mission_progress" }>,
  timestamp?: string,
): EventStreamEnvelope<Extract<ServerMessage, { type: "mission_progress" }>> {
  return buildEventStreamEnvelope({
    channel: "mission",
    event: "mission_progress",
    payload,
    timestamp,
  });
}
