/**
 * Tests for the scenario codegen pipeline (AC-436).
 *
 * Tests codegen source generation, registry routing, and unsupported family errors.
 * ScenarioRuntime tests that require secure-exec V8 isolates are in a separate
 * file (codegen-runtime.test.ts) and may be skipped in CI without isolated-vm.
 */

import { describe, it, expect } from "vitest";
import { generateScenarioSource, hasCodegen, CodegenUnsupportedFamilyError } from "../src/scenarios/codegen/index.js";
import { generateSimulationSource } from "../src/scenarios/codegen/simulation-codegen.js";
import { generateAgentTaskSource } from "../src/scenarios/codegen/agent-task-codegen.js";
import { generateArtifactEditingSource } from "../src/scenarios/codegen/artifact-editing-codegen.js";
import { generateInvestigationSource } from "../src/scenarios/codegen/investigation-codegen.js";
import { generateWorkflowSource } from "../src/scenarios/codegen/workflow-codegen.js";
import { generateNegotiationSource } from "../src/scenarios/codegen/negotiation-codegen.js";
import { generateSchemaEvolutionSource } from "../src/scenarios/codegen/schema-evolution-codegen.js";
import { generateToolFragilitySource } from "../src/scenarios/codegen/tool-fragility-codegen.js";
import { generateCoordinationSource } from "../src/scenarios/codegen/coordination-codegen.js";

interface GeneratedScenarioForTest {
  initialState(seed?: number): Record<string, unknown>;
  getAvailableActions(state: Record<string, unknown>): Array<{
    name: string;
    parameters?: Record<string, unknown>;
  }>;
  executeAction(
    state: Record<string, unknown>,
    action: { name: string; parameters?: Record<string, unknown> },
  ): { result: Record<string, unknown>; state: Record<string, unknown> };
  getResult(
    state: Record<string, unknown>,
    trace: Record<string, unknown>,
  ): {
    score: number;
    reasoning: string;
    dimensionScores: Record<string, number>;
  };
}

function loadGeneratedScenarioForTest(source: string): GeneratedScenarioForTest {
  const module: { exports: { scenario?: GeneratedScenarioForTest } } = { exports: {} };
  new Function("module", "exports", source)(module, module.exports);
  if (!module.exports.scenario) {
    throw new Error("generated source did not export scenario");
  }
  return module.exports.scenario;
}

// ---------------------------------------------------------------------------
// Registry routing
// ---------------------------------------------------------------------------

describe("codegen registry", () => {
  it("has codegen for 9 families", () => {
    const supported = [
      "simulation", "agent_task", "artifact_editing", "investigation",
      "workflow", "negotiation", "schema_evolution", "tool_fragility", "coordination",
    ];
    for (const family of supported) {
      expect(hasCodegen(family)).toBe(true);
    }
  });

  it("does not have codegen for game", () => {
    expect(hasCodegen("game")).toBe(false);
  });

  it("has codegen for operator_loop (AC-432)", () => {
    expect(hasCodegen("operator_loop")).toBe(true);
  });

  it("throws CodegenUnsupportedFamilyError for game", () => {
    expect(() => generateScenarioSource("game", {}, "test")).toThrow(CodegenUnsupportedFamilyError);
  });

  it("generates source for operator_loop (AC-432)", () => {
    const source = generateScenarioSource("operator_loop", {
      description: "test escalation",
      escalation_policy: { escalation_threshold: "high", max_escalations: 3 },
      actions: [{ name: "act", description: "d", parameters: {}, preconditions: [], effects: [] }],
    }, "test_op");
    expect(source).toContain("evaluateJudgment");
    expect(source).toContain("module.exports");
  });

  it("routes to correct codegen function", () => {
    const source = generateScenarioSource("simulation", {
      description: "test sim",
      actions: [{ name: "act1", description: "d", parameters: {}, preconditions: [], effects: [] }],
    }, "test_sim");
    expect(source).toContain("test_sim");
    expect(source).toContain("module.exports");
  });
});

// ---------------------------------------------------------------------------
// Simulation codegen
// ---------------------------------------------------------------------------

describe("simulation codegen", () => {
  const spec = {
    description: "Deploy a web service",
    environment_description: "Cloud environment",
    initial_state_description: "Empty cluster",
    success_criteria: ["all services running"],
    failure_modes: ["timeout", "crash"],
    max_steps: 15,
    actions: [
      { name: "provision", description: "Provision infra", parameters: {}, preconditions: [], effects: ["infra_ready"] },
      { name: "deploy", description: "Deploy app", parameters: {}, preconditions: ["provision"], effects: ["app_running"] },
    ],
  };

  it("generates valid JS source with scenario object", () => {
    const source = generateSimulationSource(spec, "deploy_service");
    expect(source).toContain("module.exports = { scenario }");
    expect(source).toContain("describeScenario");
    expect(source).toContain("describeEnvironment");
    expect(source).toContain("initialState");
    expect(source).toContain("getAvailableActions");
    expect(source).toContain("executeAction");
    expect(source).toContain("isTerminal");
    expect(source).toContain("getResult");
  });

  it("embeds spec data into generated source", () => {
    const source = generateSimulationSource(spec, "deploy_service");
    expect(source).toContain("Deploy a web service");
    expect(source).toContain("provision");
    expect(source).toContain("deploy");
    expect(source).toContain("15"); // maxSteps
  });

  it("generated source is syntactically valid JS", () => {
    const source = generateSimulationSource(spec, "deploy_service");
    // Should not throw
    new Function(source);
  });
});

// ---------------------------------------------------------------------------
// Agent task codegen
// ---------------------------------------------------------------------------

describe("agent task codegen", () => {
  it("generates valid JS source", () => {
    const source = generateAgentTaskSource({
      taskPrompt: "Write a poem about clouds",
      rubric: "Evaluate creativity and imagery",
      description: "Poetry task",
    }, "poetry_task");
    expect(source).toContain("getTaskPrompt");
    expect(source).toContain("evaluateOutput");
    expect(source).toContain("getRubric");
    expect(source).toContain("module.exports");
    new Function(source); // syntax check
  });
});

// ---------------------------------------------------------------------------
// Other family codegen modules
// ---------------------------------------------------------------------------

describe("artifact-editing codegen", () => {
  it("generates valid JS source", () => {
    const source = generateArtifactEditingSource({
      description: "Edit config",
      rubric: "Check validity",
      artifacts: [{ name: "config.yaml", content: "key: value", format: "yaml" }],
    }, "edit_config");
    expect(source).toContain("initialArtifacts");
    expect(source).toContain("validateArtifact");
    new Function(source);
  });
});

describe("investigation codegen", () => {
  it("generates valid JS source", () => {
    const source = generateInvestigationSource({
      description: "Debug crash",
      evidence_pool: [{ id: "log1", content: "error trace", isRedHerring: false, relevance: 0.9 }],
      correct_diagnosis: "null pointer",
      actions: [{ name: "check_logs", description: "Check logs", parameters: {}, preconditions: [], effects: [] }],
    }, "debug_crash");
    expect(source).toContain("getEvidencePool");
    expect(source).toContain("evaluateDiagnosis");
    new Function(source);
  });
});

describe("workflow codegen", () => {
  it("generates valid JS source", () => {
    const source = generateWorkflowSource({
      description: "Payment flow",
      steps: [{ name: "validate", description: "Validate input", compensationAction: "rollback" }],
      actions: [{ name: "validate", description: "Validate", parameters: {}, preconditions: [], effects: [] }],
    }, "payment_flow");
    expect(source).toContain("getWorkflowSteps");
    expect(source).toContain("executeCompensation");
    new Function(source);
  });
});

describe("negotiation codegen", () => {
  it("generates valid JS source", () => {
    const source = generateNegotiationSource({
      description: "Price negotiation",
      hidden_preferences: { minPrice: 100 },
      rounds: 3,
      actions: [{ name: "offer", description: "Make offer", parameters: {}, preconditions: [], effects: [] }],
    }, "price_negotiation");
    expect(source).toContain("getHiddenPreferences");
    expect(source).toContain("getOpponentModel");
    new Function(source);
  });
});

describe("schema-evolution codegen", () => {
  it("generates valid JS source", () => {
    const source = generateSchemaEvolutionSource({
      description: "Schema migration",
      mutations: [{ version: 1, description: "Add column", changes: {} }],
      actions: [{ name: "migrate", description: "Run migration", parameters: {}, preconditions: [], effects: [] }],
    }, "schema_migration");
    expect(source).toContain("getMutations");
    expect(source).toContain("applyMutation");
    new Function(source);
  });

  it("does not score zero-mutation schema evolution as perfect", () => {
    const source = generateSchemaEvolutionSource({
      description: "Schema migration with no discovered mutations",
      mutations: [],
      actions: [
        { name: "inspect", description: "Inspect schema", parameters: {}, preconditions: [], effects: [] },
      ],
    }, "empty_schema_migration");
    const scenario = loadGeneratedScenarioForTest(source);

    const result = scenario.getResult(scenario.initialState(7), { records: [] });

    expect(result.score).toBe(0);
    expect(result.reasoning).toContain("0/0 versions handled");
    expect(result.reasoning).toMatch(/no schema mutations/i);
    expect(result.dimensionScores.schemaCoverage).toBe(0);
    expect(result.dimensionScores.staleDetection).toBe(0);
  });

  it("records mutation lineage when schema-evolution actions execute", () => {
    const source = generateSchemaEvolutionSource({
      description: "Schema migration",
      mutations: [
        { version: 2, description: "Add priority", changes: { added: ["priority"] } },
        { version: 3, description: "Rename status", changes: { renamed: ["status"] } },
      ],
      actions: [
        { name: "inspect", description: "Inspect schema", parameters: {}, preconditions: [], effects: [] },
      ],
    }, "lineage_schema_migration");
    const scenario = loadGeneratedScenarioForTest(source);
    const state = scenario.initialState(3);
    const [action] = scenario.getAvailableActions(state);

    const { state: nextState } = scenario.executeAction(state, action);
    const result = scenario.getResult(nextState, { records: [{ action }] });

    expect(nextState.schemaVersion).toBe(1);
    expect(nextState.mutationLog).toEqual([
      { version: 2, description: "Add priority", changes: { added: ["priority"] } },
    ]);
    expect(result.score).toBe(0.5);
    expect(result.reasoning).toBe("1/2 versions handled");
  });

  it("allows sparse schema-evolution action sets to handle every mutation", () => {
    const source = generateSchemaEvolutionSource({
      description: "Schema migration with fewer actions than mutations",
      mutations: [
        { version: 2, description: "Add priority", changes: { added: ["priority"] } },
        { version: 3, description: "Rename status", changes: { renamed: ["status"] } },
      ],
      actions: [
        { name: "inspect", description: "Inspect schema", parameters: {}, preconditions: [], effects: [] },
      ],
    }, "sparse_schema_migration");
    const scenario = loadGeneratedScenarioForTest(source);
    const state = scenario.initialState(3);
    const [firstAction] = scenario.getAvailableActions(state);
    const first = scenario.executeAction(state, firstAction);

    const availableAfterFirst = scenario.getAvailableActions(first.state);
    expect(availableAfterFirst.map((action) => action.name)).toEqual(["inspect"]);

    const [secondAction] = availableAfterFirst;
    const second = scenario.executeAction(first.state, secondAction);
    const result = scenario.getResult(second.state, { records: [{ action: firstAction }] });

    expect(second.state.schemaVersion).toBe(2);
    expect(second.state.mutationLog).toEqual([
      { version: 2, description: "Add priority", changes: { added: ["priority"] } },
      { version: 3, description: "Rename status", changes: { renamed: ["status"] } },
    ]);
    expect(result.score).toBe(1);
    expect(result.reasoning).toBe("2/2 versions handled");
  });
});

describe("tool-fragility codegen", () => {
  it("generates valid JS source", () => {
    const source = generateToolFragilitySource({
      description: "API drift test",
      tool_contracts: [{ toolName: "api_call", expectedBehavior: "200 OK", driftBehavior: "timeout" }],
      actions: [{ name: "api_call", description: "Call API", parameters: {}, preconditions: [], effects: [] }],
    }, "api_drift");
    expect(source).toContain("getToolContracts");
    expect(source).toContain("injectDrift");
    new Function(source);
  });
});

describe("coordination codegen", () => {
  it("generates valid JS source", () => {
    const source = generateCoordinationSource({
      description: "Multi-agent coordination",
      workers: [{ id: "w1", role: "analyzer", partialContext: {} }],
      actions: [{ name: "analyze", description: "Analyze data", parameters: {}, preconditions: [], effects: [] }],
    }, "multi_agent");
    expect(source).toContain("getWorkerContexts");
    expect(source).toContain("recordHandoff");
    expect(source).toContain("mergeOutputs");
    new Function(source);
  });
});

// ---------------------------------------------------------------------------
// Generated source evaluation (run the generated code)
// ---------------------------------------------------------------------------

describe("generated source execution", () => {
  it("simulation scenario can be evaluated via eval", () => {
    const source = generateSimulationSource({
      description: "Test sim",
      actions: [
        { name: "step1", description: "First", parameters: {}, preconditions: [], effects: [] },
        { name: "step2", description: "Second", parameters: {}, preconditions: ["step1"], effects: [] },
      ],
      max_steps: 10,
    }, "test_eval");

    // Execute the generated code
    const module = { exports: {} as Record<string, unknown> };
    const fn = new Function("module", "exports", source);
    fn(module, module.exports);
    const scenario = (module.exports as { scenario: Record<string, (...args: unknown[]) => unknown> }).scenario;

    // Test the scenario methods
    expect(scenario.describeScenario()).toBe("Test sim");
    
    const state = scenario.initialState(42) as Record<string, unknown>;
    expect(state.seed).toBe(42);
    expect(state.step).toBe(0);

    const actions = scenario.getAvailableActions(state) as Array<{ name: string }>;
    expect(actions.length).toBe(2);

    // Execute step1
    const result1 = scenario.executeAction(state, { name: "step1", parameters: {} }) as {
      result: { success: boolean }; state: Record<string, unknown>;
    };
    expect(result1.result.success).toBe(true);

    // step2 should now work (precondition met)
    const result2 = scenario.executeAction(result1.state, { name: "step2", parameters: {} }) as {
      result: { success: boolean }; state: Record<string, unknown>;
    };
    expect(result2.result.success).toBe(true);

    // Should be terminal now (all actions completed)
    expect(scenario.isTerminal(result2.state)).toBe(true);

    // Get result
    const evalResult = scenario.getResult(result2.state, { records: [
      { result: { success: true } }, { result: { success: true } },
    ] }) as { score: number };
    expect(evalResult.score).toBeGreaterThan(0);
    expect(evalResult.score).toBeLessThanOrEqual(1);
  });

  it("simulation scenario enforces preconditions", () => {
    const source = generateSimulationSource({
      description: "Dep test",
      actions: [
        { name: "a", description: "A", parameters: {}, preconditions: [], effects: [] },
        { name: "b", description: "B", parameters: {}, preconditions: ["a"], effects: [] },
      ],
      max_steps: 10,
    }, "dep_test");

    const module = { exports: {} as Record<string, unknown> };
    new Function("module", "exports", source)(module, module.exports);
    const scenario = (module.exports as { scenario: Record<string, (...args: unknown[]) => unknown> }).scenario;

    const state = scenario.initialState(0) as Record<string, unknown>;

    // Try b without a — should fail
    const result = scenario.executeAction(state, { name: "b", parameters: {} }) as {
      result: { success: boolean; error: string };
    };
    expect(result.result.success).toBe(false);
    expect(result.result.error).toContain("precondition");
  });
});
