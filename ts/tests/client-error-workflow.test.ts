import { describe, expect, it } from "vitest";
import { z } from "zod";

import {
  buildClientErrorMessage,
  buildRecognizableClientValidationError,
  isInteractiveScenarioCommand,
  MAX_CREATE_TASK_VALIDATION_ERROR_CHARACTERS,
} from "../src/server/client-error-workflow.js";
import { parseClientMessage } from "../src/server/protocol.js";

describe("client error workflow", () => {
  it("identifies interactive scenario commands", () => {
    expect(
      isInteractiveScenarioCommand({ type: "create_scenario", description: "Draft a scenario" }),
    ).toBe(true);
    expect(
      isInteractiveScenarioCommand({
        type: "create_task",
        contract: {
          schemaVersion: 1,
          objective: "Improve the draft",
          target: "Current draft",
          deliverable: { description: "Revised draft", outputFormat: "free_text" },
          dataSources: [],
          criteria: "Evaluate quality",
          iterations: 2,
          revisionPrompt: null,
        },
        source_contents: [],
      }),
    ).toBe(true);
    expect(isInteractiveScenarioCommand({ type: "confirm_scenario" })).toBe(true);
    expect(
      isInteractiveScenarioCommand({ type: "revise_scenario", feedback: "Add guardrails" }),
    ).toBe(true);
    expect(isInteractiveScenarioCommand({ type: "cancel_scenario" })).toBe(true);
    expect(isInteractiveScenarioCommand({ type: "pause" })).toBe(false);
    expect(isInteractiveScenarioCommand(null)).toBe(false);
  });

  it("builds scenario_error messages for interactive scenario command failures", () => {
    expect(
      buildClientErrorMessage(new Error("bad scenario"), {
        type: "revise_scenario",
        feedback: "Add escalation logic",
      }),
    ).toEqual({
      type: "scenario_error",
      message: "bad scenario",
      stage: "server",
    });
  });

  it("builds generic error messages for non-scenario command failures", () => {
    expect(
      buildClientErrorMessage(new Error("bad auth"), {
        type: "whoami",
      }),
    ).toEqual({
      type: "error",
      message: "bad auth",
    });
  });

  it("labels structured task compilation failures as actionable validation errors", () => {
    expect(
      buildClientErrorMessage(new Error("task data source contentHash mismatch"), {
        type: "create_task",
        contract: {
          schemaVersion: 1,
          objective: "Improve the draft",
          target: "Current draft",
          deliverable: { description: "Revised draft", outputFormat: "free_text" },
          dataSources: [],
          criteria: "Evaluate quality",
          iterations: 2,
          revisionPrompt: null,
        },
        source_contents: [],
      }),
    ).toEqual({
      type: "scenario_error",
      message: "task data source contentHash mismatch",
      stage: "validation",
    });
  });

  it("builds bounded actionable errors for recognizable schema-invalid tasks", () => {
    const raw = {
      type: "create_task",
      contract: {
        target: "Current draft",
        deliverable: { description: "Revised draft" },
        criteria: "Evaluate quality",
      },
      source_contents: [],
    };
    let validationError: unknown;
    try {
      parseClientMessage(raw);
    } catch (error) {
      validationError = error;
    }

    const response = buildRecognizableClientValidationError(validationError, raw);
    expect(response).toMatchObject({
      type: "scenario_error",
      stage: "validation",
    });
    if (response?.type !== "scenario_error") throw new Error("expected scenario_error");
    expect(response.message).toContain("contract.objective");
    expect(response.message.length).toBeLessThanOrEqual(
      MAX_CREATE_TASK_VALIDATION_ERROR_CHARACTERS,
    );
    expect(
      buildRecognizableClientValidationError(validationError, { type: "start_run" }),
    ).toBeNull();
  });

  it("stops traversing validation issues once the response detail limit is reached", () => {
    const issues = Array.from({ length: 9 }, (_, index) => ({
      code: z.ZodIssueCode.custom,
      path: ["contract", `field${index}`],
      message: `issue ${index}`,
    })) as z.ZodIssue[];
    const error = new z.ZodError(issues);
    Object.defineProperty(issues[8]!, "code", {
      get: () => {
        throw new Error("ninth issue should not be visited");
      },
    });

    const response = buildRecognizableClientValidationError(error, {
      type: "create_task",
    });
    expect(response).toMatchObject({
      type: "scenario_error",
      stage: "validation",
    });
    if (response?.type !== "scenario_error") throw new Error("expected scenario_error");
    expect(response.message).toContain("contract.field7: issue 7");
    expect(response.message).not.toContain("issue 8");
  });

  it("preserves run and command correlation on operator failures", () => {
    expect(
      buildClientErrorMessage(new Error("scope mismatch"), {
        type: "pause",
        client_run_id: "client-run-1",
        command_id: "command-pause-1",
      }),
    ).toEqual({
      type: "error",
      message: "scope mismatch",
      client_run_id: "client-run-1",
      command_id: "command-pause-1",
    });
  });

  it("stringifies unknown thrown values", () => {
    expect(buildClientErrorMessage("boom", null)).toEqual({
      type: "error",
      message: "boom",
    });
  });
});
