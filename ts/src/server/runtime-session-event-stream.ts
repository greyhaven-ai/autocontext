import type { EventStreamEmitter } from "../loop/events.js";
import type { RuntimeSessionEventSink } from "../session/runtime-session-notifications.js";
import { buildRuntimeSessionEventNotification } from "../session/runtime-session-notifications.js";

export const RUNTIME_SESSION_EVENT_STREAM_CHANNEL = "runtime_session";
export const RUNTIME_SESSION_EVENT_STREAM_EVENT = "runtime_session_event";

type RuntimeSessionEventEmitter = Pick<EventStreamEmitter, "emit">;

export function createRuntimeSessionEventStreamSink(
  emitter: RuntimeSessionEventEmitter,
): RuntimeSessionEventSink {
  return {
    onRuntimeSessionEvent: (event, log) => {
      emitter.emit(
        RUNTIME_SESSION_EVENT_STREAM_EVENT,
        buildRuntimeSessionEventNotification(log, event),
        RUNTIME_SESSION_EVENT_STREAM_CHANNEL,
      );
    },
  };
}
