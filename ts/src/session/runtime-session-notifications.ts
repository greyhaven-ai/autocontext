import type {
  RuntimeSessionEvent,
  RuntimeSessionEventLog,
} from "./runtime-events.js";
import { summarizeRuntimeSession } from "./runtime-session-read-model.js";

export interface RuntimeSessionEventNotification extends Record<string, unknown> {
  session_id: string;
  parent_session_id: string;
  task_id: string;
  worker_id: string;
  goal: string;
  event_count: number;
  created_at: string;
  updated_at: string;
  event: {
    event_id: string;
    event_type: string;
    sequence: number;
    timestamp: string;
    payload: Record<string, unknown>;
    parent_session_id: string;
    task_id: string;
    worker_id: string;
  };
}

export interface RuntimeSessionEventSink {
  onRuntimeSessionEvent(
    event: RuntimeSessionEvent,
    log: RuntimeSessionEventLog,
  ): void;
}

export function buildRuntimeSessionEventNotification(
  log: RuntimeSessionEventLog,
  event: RuntimeSessionEvent,
): RuntimeSessionEventNotification {
  const summary = summarizeRuntimeSession(log);
  return {
    ...summary,
    event: {
      event_id: event.eventId,
      event_type: event.eventType,
      sequence: event.sequence,
      timestamp: event.timestamp,
      payload: { ...event.payload },
      parent_session_id: event.parentSessionId,
      task_id: event.taskId,
      worker_id: event.workerId,
    },
  };
}
