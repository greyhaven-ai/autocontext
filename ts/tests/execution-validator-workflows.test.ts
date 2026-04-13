import { describe, expect, it } from "vitest";

import {
  buildExecutionValidationResult,
  getMissingRequiredMethods,
  getRequiredMethods,
  loadGeneratedScenario,
  validateInitialScenarioState,
} from "../src/scenarios/codegen/execution-validator-core-workflow.js";
import {
  validateArtifactEditingScenario,
  validateOperatorLoopScenario,
} from "../src/scenarios/codegen/execution-validator-family-workflow.js";

describe("execution validator workflows", () => {
  it("loads generated scenarios and reports missing required methods by family", () => {
    const loaded = loadGeneratedScenario(`module.exports = { scenario: { initialState() { return {}; } } };`);
    expect(loaded.error).toBeUndefined();
    expect(loaded.scenario).not.toBeNull();

    expect(getRequiredMethods("operator_loop")).toContain("requestClarification");
    expect(getMissingRequiredMethods(loaded.scenario!, "simulation")).toEqual([
      "describeScenario",
      "describeEnvironment",
      "getAvailableActions",
      "executeAction",
      "isTerminal",
      "getResult",
      "getRubric",
    ]);
  });

  it("validates initial state and assembles result payloads", () => {
    const context = { errors: [], executedMethods: [] as string[] };
    const state = validateInitialScenarioState(
      { initialState: () => ({ step: 0 }) },
      context,
    );
    expect(state).toEqual({ step: 0 });
    expect(context.executedMethods).toContain("initialState");

    const result = buildExecutionValidationResult(performance.now() - 5, context);
    expect(result.valid).toBe(true);
    expect(result.durationMs).toBeTypeOf("number");
  });

  it("validates operator-loop and artifact-editing family-specific hooks", () => {
    const operatorContext = { errors: [], executedMethods: [] as string[] };
    validateOperatorLoopScenario(
      {
        describeScenario: () => "scenario",
        describeEnvironment: () => ({ name: "env" }),
        getRubric: () => "rubric",
        getAvailableActions: () => [{ name: "inspect" }],
        executeAction: (...args: unknown[]) => ({ result: { success: true }, state: args[0] as Record<string, unknown> }),
        isTerminal: () => true,
        getResult: () => ({ score: 1, reasoning: "ok" }),
        requestClarification: (...args: unknown[]) => ({ ...(args[0] as Record<string, unknown>) }),
        escalate: (...args: unknown[]) => ({ ...(args[0] as Record<string, unknown>) }),
      },
      { seed: 42 },
      operatorContext,
    );
    expect(operatorContext.errors).toEqual([]);
    expect(operatorContext.executedMethods).toContain("requestClarification");
    expect(operatorContext.executedMethods).toContain("escalate");

    const artifactContext = { errors: [], executedMethods: [] as string[] };
    validateArtifactEditingScenario(
      {
        describeTask: () => "Edit artifact",
        initialArtifacts: () => [],
        getRubric: () => "rubric",
        getEditPrompt: () => "prompt",
        validateArtifact: () => ({ valid: true }),
      },
      { seed: 1 },
      artifactContext,
    );
    expect(artifactContext.errors).toContain("initialArtifacts must return at least one artifact");
    expect(artifactContext.executedMethods).toContain("validateArtifact");
  });
});
