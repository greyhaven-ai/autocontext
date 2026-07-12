import type { ClientMessage, ServerMessage } from "./protocol.js";

export function isInteractiveScenarioCommand(
  message: ClientMessage | Record<string, unknown> | null,
): message is Extract<
  ClientMessage,
  { type: "create_scenario" | "confirm_scenario" | "revise_scenario" | "cancel_scenario" }
> {
  const type = message && typeof message === "object" ? message.type : null;
  return (
    type === "create_scenario" ||
    type === "confirm_scenario" ||
    type === "revise_scenario" ||
    type === "cancel_scenario"
  );
}

export function buildClientErrorMessage(
  error: unknown,
  message: ClientMessage | null,
): ServerMessage {
  const detail = error instanceof Error ? error.message : String(error);
  if (isInteractiveScenarioCommand(message)) {
    return {
      type: "scenario_error",
      message: detail,
      stage: "server",
    };
  }
  const correlation = commandCorrelation(message);
  return {
    type: "error",
    message: detail,
    ...correlation,
  };
}

function commandCorrelation(message: ClientMessage | null): {
  client_run_id?: string;
  command_id?: string;
} {
  if (!message) return {};
  const clientRunId = "client_run_id" in message ? message.client_run_id : undefined;
  const commandId = "command_id" in message ? message.command_id : undefined;
  return {
    ...(clientRunId ? { client_run_id: clientRunId } : {}),
    ...(commandId ? { command_id: commandId } : {}),
  };
}
