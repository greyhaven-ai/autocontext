import type { ClientMessage, ServerMessage } from "./protocol.js";

export function isInteractiveScenarioCommand(
  message: ClientMessage | Record<string, unknown> | null,
): message is Extract<
  ClientMessage,
  { type: "create_scenario" | "confirm_scenario" | "revise_scenario" | "cancel_scenario" }
> {
  const type = message && typeof message === "object" ? message.type : null;
  return (
    type === "create_scenario"
    || type === "confirm_scenario"
    || type === "revise_scenario"
    || type === "cancel_scenario"
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
  return {
    type: "error",
    message: detail,
  };
}
