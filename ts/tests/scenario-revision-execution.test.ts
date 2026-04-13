import { describe, expect, it } from "vitest";

import { executeScenarioRevision } from "../src/scenarios/scenario-revision-execution.js";

describe("scenario revision execution", () => {
  it("parses provider JSON, merges with the original spec, and normalizes the result", async () => {
    const result = await executeScenarioRevision({
      currentSpec: {
        description: "Old task",
        taskPrompt: "Do X",
        rubric: "Evaluate X",
      },
      family: "agent_task",
      prompt: "revise it",
      provider: {
        complete: async () => ({
          text: JSON.stringify({
            description: "Improved task",
            taskPrompt: "Do X better",
          }),
        }),
        defaultModel: () => "test-model",
      } as never,
    });

    expect(result.changesApplied).toBe(true);
    expect(result.revised).toMatchObject({
      description: "Improved task",
      taskPrompt: "Do X better",
      judgeRubric: "Evaluate X",
    });
  });

  it("returns the original spec when the provider response is not valid JSON", async () => {
    const original = {
      description: "Original",
      taskPrompt: "Do Y",
      rubric: "Evaluate Y",
    };

    const result = await executeScenarioRevision({
      currentSpec: original,
      family: "agent_task",
      prompt: "revise it",
      provider: {
        complete: async () => ({ text: "not json" }),
        defaultModel: () => "test-model",
      } as never,
    });

    expect(result.changesApplied).toBe(false);
    expect(result.revised).toEqual(original);
    expect(result.error).toContain("valid JSON");
  });

  it("returns the original spec when normalized family validation fails", async () => {
    const original = {
      description: "Old sim",
      environment_description: "Env",
      initial_state_description: "State",
      success_criteria: ["all steps done", "rollback possible"],
      failure_modes: [],
      max_steps: 10,
      actions: [
        { name: "step1", description: "First", parameters: {}, preconditions: [], effects: [] },
        { name: "step2", description: "Second", parameters: {}, preconditions: ["step1"], effects: [] },
      ],
    };

    const result = await executeScenarioRevision({
      currentSpec: original,
      family: "simulation",
      prompt: "revise it",
      provider: {
        complete: async () => ({
          text: JSON.stringify({
            actions: [{ name: "only_one", description: "Only step", parameters: {}, preconditions: [], effects: [] }],
            max_steps: "twenty",
          }),
        }),
        defaultModel: () => "test-model",
      } as never,
    });

    expect(result.changesApplied).toBe(false);
    expect(result.revised).toEqual(original);
    expect(result.error).toContain("maxSteps");
  });
});
