import {
  RuntimeSessionEventType,
  type RuntimeSessionEventLog,
} from "../../session/runtime-events.js";
import type {
  RuntimeActivationAuditEvent,
  RuntimeActivationAuditEventSink,
} from "./types.js";

export function createRuntimeSessionActivationAuditEventSink(
  log: RuntimeSessionEventLog,
): RuntimeActivationAuditEventSink {
  return {
    onRuntimeActivationAuditEvent(event) {
      log.append(RuntimeSessionEventType.RUNTIME_ACTIVATION, auditPayload(event));
    },
  };
}

function auditPayload(event: RuntimeActivationAuditEvent): Record<string, unknown> {
  return Object.fromEntries(Object.entries({
    transactionId: event.transactionId,
    operation: event.operation,
    candidateArtifactId: event.candidateArtifactId ?? undefined,
    priorArtifactId: event.priorArtifactId ?? undefined,
    stage: event.stage,
    outcome: event.outcome,
    failureCode: event.failureCode,
  }).filter((entry) => entry[1] !== undefined));
}
