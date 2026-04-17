import { describe, expect, it } from "vitest";

import {
  countImportedScenarioFamilySpecificFields,
  resolveImportedScenarioFamily,
} from "../src/cli/new-scenario-family-resolution.js";

describe("new-scenario family resolution", () => {
  it("counts family-specific imported fields excluding core prompt fields", () => {
    expect(
      countImportedScenarioFamilySpecificFields({
        taskPrompt: "Summarize the incident report.",
        rubric: "Clarity and factual accuracy",
        description: "Evaluate incident summaries",
        actions: [],
      }),
    ).toBe(1);
  });

  it("preserves agent-task fallback semantics and validates requested families", () => {
    expect(
      resolveImportedScenarioFamily({
        spec: {
          name: "fresh_saved_task",
          family: "workflow",
          taskPrompt: "Summarize the incident report.",
          rubric: "Clarity and factual accuracy",
          description: "Evaluate incident summaries",
        },
        description: "Evaluate incident summaries",
        taskPrompt: "Summarize the incident report.",
        detectScenarioFamily: () => "workflow",
        isScenarioFamilyName: (value: string) => ["agent_task", "workflow"].includes(value),
        validFamilies: ["agent_task", "workflow"],
      }),
    ).toMatchObject({
      family: "agent_task",
    });

    expect(
      resolveImportedScenarioFamily({
        spec: {
          name: "workflow_saved_task",
          family: "workflow",
          taskPrompt: "Summarize the incident report.",
          rubric: "Clarity and factual accuracy",
          description: "Evaluate incident summaries",
          steps: [],
        },
        description: "Evaluate incident summaries",
        taskPrompt: "Summarize the incident report.",
        detectScenarioFamily: () => "workflow",
        isScenarioFamilyName: (value: string) => ["agent_task", "workflow"].includes(value),
        validFamilies: ["agent_task", "workflow"],
      }),
    ).toMatchObject({
      family: "workflow",
    });

    expect(
      resolveImportedScenarioFamily({
        spec: {
          name: "simulation_saved_task",
          family: "simulation",
          taskPrompt: "Handle the crisis response.",
          rubric: "Keep the system stable.",
          description: "A simulation import with no initial actions.",
          actions: [],
        },
        description: "A simulation import with no initial actions.",
        taskPrompt: "Handle the crisis response.",
        detectScenarioFamily: () => "simulation",
        isScenarioFamilyName: (value: string) => ["agent_task", "simulation"].includes(value),
        validFamilies: ["agent_task", "simulation"],
      }),
    ).toMatchObject({
      family: "agent_task",
    });

    expect(
      resolveImportedScenarioFamily({
        spec: {
          name: "workflow_with_missing_actions",
          family: "workflow",
          taskPrompt: "Run the checkout workflow.",
          rubric: "Verify compensation and side-effect handling.",
          description: "A workflow import whose actions need repair.",
          workflow_steps: [
            {
              name: "charge_card",
              description: "Charge the customer",
              idempotent: false,
              reversible: true,
              compensation: "refund_card",
            },
          ],
          success_criteria: ["Complete the checkout", "Rollback failed charges"],
          actions: [],
        },
        description: "A workflow import whose actions need repair.",
        taskPrompt: "Run the checkout workflow.",
        detectScenarioFamily: () => "workflow",
        isScenarioFamilyName: (value: string) => ["agent_task", "workflow"].includes(value),
        validFamilies: ["agent_task", "workflow"],
      }),
    ).toMatchObject({
      family: "workflow",
    });

    expect(() =>
      resolveImportedScenarioFamily({
        spec: {
          name: "bad_family_task",
          family: "bogus",
          taskPrompt: "Summarize the incident report.",
          rubric: "Clarity and factual accuracy",
          description: "Evaluate incident summaries",
        },
        description: "Evaluate incident summaries",
        taskPrompt: "Summarize the incident report.",
        detectScenarioFamily: () => "workflow",
        isScenarioFamilyName: (value: string) => ["agent_task", "workflow"].includes(value),
        validFamilies: ["agent_task", "workflow"],
      }),
    ).toThrow("Error: family must be one of agent_task, workflow");
  });
});
