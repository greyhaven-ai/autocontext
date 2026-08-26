import { z } from "zod";
import type { ClientMessage, ServerMessage } from "./protocol.js";

export const MAX_CREATE_TASK_VALIDATION_ERROR_CHARACTERS = 1_024;
const MAX_CREATE_TASK_VALIDATION_ISSUES = 8;

export function isInteractiveScenarioCommand(
  message: ClientMessage | Record<string, unknown> | null,
): message is Extract<
  ClientMessage,
  {
    type:
      | "create_scenario"
      | "create_task"
      | "confirm_scenario"
      | "revise_scenario"
      | "cancel_scenario";
  }
> {
  const type = message && typeof message === "object" ? message.type : null;
  return (
    type === "create_scenario" ||
    type === "create_task" ||
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
      stage: message.type === "create_task" ? "validation" : "server",
    };
  }
  const correlation = commandCorrelation(message);
  return {
    type: "error",
    message: detail,
    ...correlation,
  };
}

export function buildRecognizableClientValidationError(
  error: unknown,
  message: Record<string, unknown> | null,
): ServerMessage | null {
  if (message?.type !== "create_task") return null;

  const details = error instanceof z.ZodError
    ? collectZodIssueDetails(error.issues, MAX_CREATE_TASK_VALIDATION_ISSUES)
    : [];
  const suffix = details.length > 0
    ? details.join("; ")
    : "the command does not match the required structured-task schema";
  const validationMessage = `Invalid create_task: ${suffix}`
    .replace(/\s+/g, " ")
    .slice(0, MAX_CREATE_TASK_VALIDATION_ERROR_CHARACTERS);
  return {
    type: "scenario_error",
    message: validationMessage,
    stage: "validation",
  };
}

function collectZodIssueDetails(
  issues: readonly z.ZodIssue[],
  maxDetails: number,
): string[] {
  const details: string[] = [];
  const seen = new Set<string>();
  const visit = (issue: z.ZodIssue): void => {
    if (details.length >= maxDetails) return;
    if (issue.code === z.ZodIssueCode.invalid_union) {
      for (const unionError of issue.unionErrors) {
        for (const nestedIssue of unionError.issues) {
          visit(nestedIssue);
          if (details.length >= maxDetails) return;
        }
      }
      return;
    }
    const path = issue.path.length > 0
      ? issue.path
          .map((segment) => String(segment).slice(0, 128))
          .join(".")
          .slice(0, 512)
      : "message";
    const detail = `${path}: ${issue.message.slice(
      0,
      MAX_CREATE_TASK_VALIDATION_ERROR_CHARACTERS,
    )}`;
    if (!seen.has(detail)) {
      seen.add(detail);
      details.push(detail);
    }
  };
  for (const issue of issues) {
    visit(issue);
    if (details.length >= maxDetails) break;
  }
  return details;
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
