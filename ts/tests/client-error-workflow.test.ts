import { describe, expect, it } from "vitest";

import {
  buildClientErrorMessage,
  isInteractiveScenarioCommand,
} from "../src/server/client-error-workflow.js";

describe("client error workflow", () => {
  it("identifies interactive scenario commands", () => {
    expect(isInteractiveScenarioCommand({ type: "create_scenario", description: "Draft a scenario" })).toBe(true);
    expect(isInteractiveScenarioCommand({ type: "confirm_scenario" })).toBe(true);
    expect(isInteractiveScenarioCommand({ type: "revise_scenario", feedback: "Add guardrails" })).toBe(true);
    expect(isInteractiveScenarioCommand({ type: "cancel_scenario" })).toBe(true);
    expect(isInteractiveScenarioCommand({ type: "pause" })).toBe(false);
    expect(isInteractiveScenarioCommand(null)).toBe(false);
  });

  it("builds scenario_error messages for interactive scenario command failures", () => {
    expect(buildClientErrorMessage(new Error("bad scenario"), {
      type: "revise_scenario",
      feedback: "Add escalation logic",
    })).toEqual({
      type: "scenario_error",
      message: "bad scenario",
      stage: "server",
    });
  });

  it("builds generic error messages for non-scenario command failures", () => {
    expect(buildClientErrorMessage(new Error("bad auth"), {
      type: "whoami",
    })).toEqual({
      type: "error",
      message: "bad auth",
    });
  });

  it("stringifies unknown thrown values", () => {
    expect(buildClientErrorMessage("boom", null)).toEqual({
      type: "error",
      message: "boom",
    });
  });
});
