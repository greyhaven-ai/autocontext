import type {
  RuntimeGrantEvent,
  RuntimeGrantEventSink,
} from "../runtimes/workspace-env.js";
import { RuntimeSessionEventType } from "./runtime-events.js";
import type { RuntimeSessionEventLog } from "./runtime-events.js";

export type RuntimeGrantEventCorrelation =
  | Record<string, unknown>
  | (() => Record<string, unknown>);

export function createRuntimeSessionGrantEventSink(
  log: RuntimeSessionEventLog,
  correlation: RuntimeGrantEventCorrelation = {},
): RuntimeGrantEventSink {
  return {
    onRuntimeGrantEvent: (event) => {
      log.append(runtimeSessionEventTypeForGrant(event), {
        ...runtimeGrantEventPayload(event),
        ...resolveCorrelation(correlation),
      });
    },
  };
}

function runtimeSessionEventTypeForGrant(event: RuntimeGrantEvent): RuntimeSessionEventType {
  return event.kind === "tool"
    ? RuntimeSessionEventType.TOOL_CALL
    : RuntimeSessionEventType.SHELL_COMMAND;
}

function runtimeGrantEventPayload(event: RuntimeGrantEvent): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    phase: event.phase,
    cwd: event.cwd,
    argsSummary: event.argsSummary,
    redaction: event.redaction,
  };
  if (event.kind === "tool") {
    payload.tool = event.name;
    payload.toolName = event.name;
  } else {
    payload.command = event.name;
    payload.commandName = event.name;
  }
  if (event.exitCode !== undefined) payload.exitCode = event.exitCode;
  if (event.stdout !== undefined) payload.stdout = event.stdout;
  if (event.stderr !== undefined) payload.stderr = event.stderr;
  if (event.error !== undefined) payload.error = event.error;
  if (event.provenance) payload.provenance = event.provenance;
  return payload;
}

function resolveCorrelation(
  correlation: RuntimeGrantEventCorrelation,
): Record<string, unknown> {
  return typeof correlation === "function" ? correlation() : correlation;
}
