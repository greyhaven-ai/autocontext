import type {
  RuntimeComponentGraphEvent,
  RuntimeComponentGraphEventSink,
} from "../runtimes/component-graph.js";
import {
  RuntimeSessionEventType,
  type RuntimeSessionEventLog,
} from "./runtime-events.js";

export function createRuntimeSessionComponentGraphEventSink(
  log: RuntimeSessionEventLog,
): RuntimeComponentGraphEventSink {
  return {
    onRuntimeComponentGraphEvent(event) {
      log.append(RuntimeSessionEventType.COMPONENT_GRAPH, componentGraphEventPayload(event));
    },
  };
}

function componentGraphEventPayload(
  event: RuntimeComponentGraphEvent,
): Record<string, unknown> {
  return withoutUndefined({
    revision: event.revision,
    operation: event.operation,
    outcome: event.outcome,
    componentId: event.componentId,
    instanceId: event.instanceId,
    capabilityId: event.capabilityId,
    providerComponentId: event.providerComponentId,
    providerInstanceId: event.providerInstanceId,
    reason: event.reason,
  });
}

function withoutUndefined(
  value: Record<string, string | number | undefined>,
): Record<string, string | number> {
  return Object.fromEntries(
    Object.entries(value).filter((entry): entry is [string, string | number] =>
      entry[1] !== undefined,
    ),
  );
}
