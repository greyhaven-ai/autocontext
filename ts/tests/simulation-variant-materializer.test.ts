import { describe, expect, it, beforeEach, afterEach } from "vitest";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  applySimulationVariableOverrides,
  buildSimulationVariant,
  loadReplaySimulationVariant,
  parseSimulationSpecJson,
} from "../src/simulation/variant-materializer.js";
import { persistSimulationArtifacts } from "../src/simulation/artifact-store.js";
import type { LLMProvider } from "../src/types/index.js";

function mockProvider(text: string): LLMProvider {
  return {
    complete: async () => ({ text }),
    defaultModel: () => "test-model",
  } as unknown as LLMProvider;
}

let tmpDir: string;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "ac-sim-variant-"));
});

afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

describe("simulation variant materializer", () => {
  it("parses simulation spec JSON from a response with surrounding text", () => {
    expect(parseSimulationSpecJson("Here you go:\n{\"description\":\"test\",\"actions\":[]}\nThanks")).toEqual({
      description: "test",
      actions: [],
    });
  });

  it("applies family-specific variable overrides and preserves passthrough variables", () => {
    expect(
      applySimulationVariableOverrides(
        {
          actions: [],
          escalation_policy: { escalation_threshold: "medium", max_escalations: 2 },
        },
        "operator_loop",
        {
          max_steps: 4,
          escalation_threshold: "high",
          max_escalations: 5,
          budget: 100,
        },
      ),
    ).toEqual({
      actions: [],
      max_steps: 4,
      escalation_policy: { escalation_threshold: "high", max_escalations: 5 },
      simulation_variables: { budget: 100 },
    });
  });

  it("builds a validated simulation variant from provider output", async () => {
    const provider = mockProvider(
      JSON.stringify({
        description: "Deploy service",
        environment_description: "Prod",
        initial_state_description: "Start",
        success_criteria: ["done"],
        failure_modes: ["timeout"],
        max_steps: 10,
        actions: [
          { name: "step_a", description: "A", parameters: {}, preconditions: [], effects: [] },
        ],
      }),
    );

    const variant = await buildSimulationVariant({
      provider,
      description: "Simulate deployment",
      family: "simulation",
      name: "deploy_test",
      variables: { max_steps: 2, budget: 100 },
    });

    expect(variant.spec.max_steps).toBe(2);
    expect(variant.spec.simulation_variables).toEqual({ budget: 100 });
    expect(variant.source).toContain("module.exports");
    expect(variant.source).toContain("deploy_test");
  });

  it("loads replay variants from persisted source when regeneration is not required", async () => {
    const scenarioDir = persistSimulationArtifacts({
      knowledgeRoot: tmpDir,
      name: "deploy_test",
      family: "simulation",
      spec: {
        description: "Deploy service",
        environment_description: "Prod",
        initial_state_description: "Start",
        success_criteria: ["done"],
        failure_modes: ["timeout"],
        max_steps: 3,
        actions: [{ name: "step_a", description: "A", parameters: {}, preconditions: [], effects: [] }],
      },
      source: "module.exports = { scenario: { initialState(){return{};}, isTerminal(){return true;}, getAvailableActions(){return[];}, executeAction(){return {result:{}, state:{}};}, getResult(){return {score:1, reasoning:'ok', dimensionScores:{}};} } };",
    });

    const variant = await loadReplaySimulationVariant({
      scenarioDir,
      family: "simulation",
      name: "deploy_test",
      variables: { max_steps: 3 },
      regenerate: false,
    });

    expect(variant.variables).toEqual({ max_steps: 3 });
    expect(variant.source).toBe(readFileSync(join(scenarioDir, "scenario.js"), "utf-8"));
  });

  it("regenerates replay variants when overrides require updated source", async () => {
    const scenarioDir = persistSimulationArtifacts({
      knowledgeRoot: tmpDir,
      name: "override_test",
      family: "simulation",
      spec: {
        description: "Override service",
        environment_description: "Prod",
        initial_state_description: "Start",
        success_criteria: ["done"],
        failure_modes: ["timeout"],
        max_steps: 3,
        actions: [{ name: "step_a", description: "A", parameters: {}, preconditions: [], effects: [] }],
      },
      source: "module.exports = { scenario: { initialState(){return{};}, isTerminal(){return true;}, getAvailableActions(){return[];}, executeAction(){return {result:{}, state:{}};}, getResult(){return {score:1, reasoning:'ok', dimensionScores:{}};} } };",
    });

    const variant = await loadReplaySimulationVariant({
      scenarioDir,
      family: "simulation",
      name: "override_test",
      variables: { max_steps: 7, budget: 50 },
      regenerate: true,
    });

    expect(variant.spec.max_steps).toBe(7);
    expect(variant.spec.simulation_variables).toEqual({ budget: 50 });
    expect(variant.source).toContain("override_test");
  });
});
