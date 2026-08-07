import { describe, expect, it, vi } from "vitest";

import { planMaterializedScenarioFamily } from "../src/scenarios/materialize-family-planning.js";
import type { ScenarioFamilyName } from "../src/scenarios/families.js";

describe("materialize family planning", () => {
  it("normalizes and validates agent-task specs into persisted planning data", async () => {
    const result = await planMaterializedScenarioFamily(
      {
        family: "agent_task",
        name: "task_one",
        scenarioType: "agent_task",
        healedSpec: {
          taskPrompt: "Write a poem",
          rubric: "Judge creativity",
          description: "Poetry task",
        },
      },
      {
        hasCodegen: vi.fn(() => false),
        generateScenarioSource: vi.fn(),
        validateGeneratedScenario: vi.fn() as any,
      },
    );

    expect(result.errors).toEqual([]);
    expect(result.generatedSource).toBe(false);
    expect(result.agentTaskSpec).toMatchObject({
      taskPrompt: "Write a poem",
      judgeRubric: "Judge creativity",
    });
    expect(result.persistedSpec).toMatchObject({
      name: "task_one",
      family: "agent_task",
      taskPrompt: "Write a poem",
      judgeRubric: "Judge creativity",
      rubric: "Judge creativity",
    });
  });

  it("plans validated codegen family materialization", async () => {
    const result = await planMaterializedScenarioFamily(
      {
        family: "simulation",
        name: "sim_one",
        scenarioType: "simulation",
        healedSpec: { description: "Generated sim" },
      },
      {
        hasCodegen: vi.fn(() => true),
        generateScenarioSource: vi.fn(() => "module.exports = { scenario: {} }"),
        validateGeneratedScenario: vi.fn(async () => ({
          valid: true,
          errors: [],
          executedMethods: [],
          durationMs: 1,
        })) as any,
      },
    );

    expect(result.errors).toEqual([]);
    expect(result.generatedSource).toBe(true);
    expect(result.source).toContain("module.exports");
    expect(result.agentTaskSpec).toBeNull();
  });

  it("reports unsupported-family planning errors", async () => {
    const result = await planMaterializedScenarioFamily(
      {
        // Deliberately invalid family: this test asserts the planner rejects a
        // family outside ScenarioFamilyName, so the value cannot be well-typed.
        family: "unknown_family" as unknown as ScenarioFamilyName,
        name: "mystery",
        scenarioType: "agent_task",
        healedSpec: { taskPrompt: "Do work" },
      },
      {
        hasCodegen: vi.fn(() => false),
        generateScenarioSource: vi.fn(),
        validateGeneratedScenario: vi.fn() as any,
      },
    );

    expect(result.errors).toEqual([
      "custom scenario materialization is not supported for family 'unknown_family'",
    ]);
    expect(result.generatedSource).toBe(false);
  });
});
