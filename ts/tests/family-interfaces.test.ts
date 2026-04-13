/**
 * Tests for AC-380: runtime interface contracts for all 11 scenario families.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const simulationBase = {
  describeScenario: () => "scenario",
  describeEnvironment: () => ({}),
  initialState: () => ({}),
  getAvailableActions: () => [],
  executeAction: () => [{}, {}] as [unknown, Record<string, unknown>],
  isTerminal: () => false,
  evaluateTrace: () => ({}),
  getRubric: () => "rubric",
};

describe("Scenario family interfaces", () => {
  it("exports all 11 family guards plus assertion helpers", async () => {
    const mod = await import("../src/scenarios/family-interfaces.js");

    expect(mod.isGameScenario).toBeDefined();
    expect(mod.isAgentTask).toBeDefined();
    expect(mod.isSimulation).toBeDefined();
    expect(mod.isNegotiation).toBeDefined();
    expect(mod.isInvestigation).toBeDefined();
    expect(mod.isWorkflow).toBeDefined();
    expect(mod.isSchemaEvolution).toBeDefined();
    expect(mod.isToolFragility).toBeDefined();
    expect(mod.isOperatorLoop).toBeDefined();
    expect(mod.isCoordination).toBeDefined();
    expect(mod.isArtifactEditing).toBeDefined();
    expect(mod.assertFamilyContract).toBeDefined();
    expect(mod.detectFamily).toBeDefined();
  });

  it("detects every promised family with family-specific methods", async () => {
    const {
      detectFamily,
      isArtifactEditing,
      isCoordination,
      isInvestigation,
      isNegotiation,
      isOperatorLoop,
      isSchemaEvolution,
      isSimulation,
      isToolFragility,
      isWorkflow,
    } = await import("../src/scenarios/family-interfaces.js");

    const simulation = { ...simulationBase };
    const negotiation = {
      ...simulationBase,
      getHiddenPreferences: () => ({}),
      getRounds: () => [],
      getOpponentModel: () => null,
      updateOpponentModel: () => ({}),
      evaluateNegotiation: () => ({}),
    };
    const investigation = {
      ...simulationBase,
      getEvidencePool: () => [],
      evaluateEvidenceChain: () => 0.5,
      evaluateDiagnosis: () => ({}),
    };
    const workflow = {
      ...simulationBase,
      getWorkflowSteps: () => [],
      executeStep: () => ({}),
      executeCompensation: () => ({}),
      getSideEffects: () => [],
      evaluateWorkflow: () => ({}),
    };
    const schemaEvolution = {
      ...simulationBase,
      getMutations: () => [],
      getSchemaVersion: () => 1,
      getMutationLog: () => [],
      applyMutation: () => ({}),
      checkContextValidity: () => [],
      evaluateAdaptation: () => ({}),
    };
    const toolFragility = {
      ...simulationBase,
      getToolContracts: () => [],
      getDriftLog: () => [],
      injectDrift: () => ({}),
      attributeFailure: () => ({}),
      evaluateFragility: () => ({}),
    };
    const operatorLoop = {
      ...simulationBase,
      getEscalationLog: () => [],
      getClarificationLog: () => [],
      escalate: () => ({}),
      requestClarification: () => ({}),
      evaluateJudgment: () => ({}),
    };
    const coordination = {
      ...simulationBase,
      getWorkerContexts: () => [],
      getHandoffLog: () => [],
      recordHandoff: () => ({}),
      mergeOutputs: () => ({}),
      evaluateCoordination: () => ({}),
    };
    const artifactEditing = {
      describeTask: () => "task",
      getRubric: () => "rubric",
      initialArtifacts: () => [],
      getEditPrompt: () => "prompt",
      validateArtifact: () => ({}),
      evaluateEdits: () => ({}),
    };

    // Keep explicit guard assertions so we catch drift in the public exports.
    expect(isSimulation(simulation)).toBe(true);
    expect(isNegotiation(negotiation)).toBe(true);
    expect(isInvestigation(investigation)).toBe(true);
    expect(isWorkflow(workflow)).toBe(true);
    expect(isSchemaEvolution(schemaEvolution)).toBe(true);
    expect(isToolFragility(toolFragility)).toBe(true);
    expect(isOperatorLoop(operatorLoop)).toBe(true);
    expect(isCoordination(coordination)).toBe(true);
    expect(isArtifactEditing(artifactEditing)).toBe(true);

    expect(detectFamily(simulation)).toBe("simulation");
    expect(detectFamily(negotiation)).toBe("negotiation");
    expect(detectFamily(investigation)).toBe("investigation");
    expect(detectFamily(workflow)).toBe("workflow");
    expect(detectFamily(schemaEvolution)).toBe("schema_evolution");
    expect(detectFamily(toolFragility)).toBe("tool_fragility");
    expect(detectFamily(operatorLoop)).toBe("operator_loop");
    expect(detectFamily(coordination)).toBe("coordination");
    expect(detectFamily(artifactEditing)).toBe("artifact_editing");
  });

  it("detects agent-task and game families via the existing runtime contracts", async () => {
    const { detectFamily, isAgentTask, isGameScenario } =
      await import("../src/scenarios/family-interfaces.js");
    const { createAgentTask } = await import("../src/scenarios/agent-task-factory.js");
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");

    const agentTask = createAgentTask({
      name: "saved_task",
      spec: {
        taskPrompt: "Summarize the incident.",
        judgeRubric: "Score clarity and correctness.",
        outputFormat: "free_text",
        judgeModel: "",
        maxRounds: 1,
        qualityThreshold: 0.9,
      },
    });
    const game = new GridCtfScenario();

    expect(isAgentTask(agentTask)).toBe(true);
    expect(isGameScenario(game)).toBe(true);
    expect(detectFamily(agentTask)).toBe("agent_task");
    expect(detectFamily(game)).toBe("game");
  });

  it("exports family contracts directly instead of routing through a facade barrel", () => {
    const source = readFileSync(
      join(import.meta.dirname, "..", "src", "scenarios", "family-interfaces.ts"),
      "utf-8",
    );

    expect(source).not.toContain('export * from "./family-interface-public-facade.js";');
  });

  it("assertFamilyContract throws a helpful error for mismatched families", async () => {
    const { assertFamilyContract } = await import("../src/scenarios/family-interfaces.js");

    expect(() => assertFamilyContract(simulationBase, "coordination", "test scenario")).toThrow(
      /test scenario does not satisfy 'coordination' contract/i,
    );
    expect(() => assertFamilyContract({}, "schema_evolution")).toThrow(/getMutations/i);
  });
});
