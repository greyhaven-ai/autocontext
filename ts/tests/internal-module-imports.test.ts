import { describe, expect, it } from "vitest";

describe("direct internal module imports", () => {
  it("loads codegen registry helpers without going through the barrel", async () => {
    const { generateScenarioSource, hasCodegen } = await import("../src/scenarios/codegen/registry.js");

    expect(hasCodegen("simulation")).toBe(true);
    const source = generateScenarioSource(
      "simulation",
      {
        description: "test sim",
        actions: [{ name: "act", description: "desc", parameters: {}, preconditions: [], effects: [] }],
      },
      "direct_registry_test",
    );
    expect(source).toContain("module.exports");
  });

  it("loads the LLM judge implementation directly", async () => {
    const { DEFAULT_FACTUAL_CONFIDENCE, detectGeneratedDimensions } = await import("../src/judge/llm-judge.js");

    expect(DEFAULT_FACTUAL_CONFIDENCE).toBe(0.5);
    expect(detectGeneratedDimensions(["clarity_score"], "Evaluate clarity and accuracy")).toBe(false);
  });
});
