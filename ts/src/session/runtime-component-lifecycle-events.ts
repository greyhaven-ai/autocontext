import type {
  RuntimeComponentLifecycleEvent,
  RuntimeComponentLifecycleEventSink,
} from "../runtimes/component-lifecycle.js";
import {
  RuntimeSessionEventType,
  type RuntimeSessionEventLog,
} from "./runtime-events.js";

export function createRuntimeSessionComponentLifecycleEventSink(
  log: RuntimeSessionEventLog,
): RuntimeComponentLifecycleEventSink {
  return {
    onRuntimeComponentLifecycleEvent(event) {
      log.append(
        RuntimeSessionEventType.COMPONENT_LIFECYCLE,
        runtimeComponentLifecycleEventPayload(event),
      );
    },
  };
}

function runtimeComponentLifecycleEventPayload(
  event: RuntimeComponentLifecycleEvent,
): Record<string, unknown> {
  return {
    componentId: event.componentId,
    previousState: event.previousState,
    state: event.state,
    operation: event.operation,
    outcome: event.outcome,
  };
}
