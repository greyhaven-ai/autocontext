import { describe, expect, it } from "vitest";

import type {
  AgentTaskInterface,
  ArtifactEditingInterface,
  CoordinationInterface,
  GameScenarioInterface,
  InvestigationInterface,
  NegotiationInterface,
  OperatorLoopInterface,
  ScenarioFamilyName,
  SchemaEvolutionInterface,
  SimulationInterface,
  ToolFragilityInterface,
  WorkflowInterface,
} from "../src/scenarios/family-interface-types.js";

describe("family interface types", () => {
  it("supports compile-time access to the public family interface types", async () => {
    const { createAgentTask } = await import("../src/scenarios/agent-task-factory.js");
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");

    const family: ScenarioFamilyName = "simulation";
    const simulation: SimulationInterface = {
      describeScenario: () => "scenario",
      describeEnvironment: () => ({}),
      initialState: () => ({}),
      getAvailableActions: () => [],
      executeAction: () => [{}, {}],
      isTerminal: () => false,
      evaluateTrace: () => ({}),
      getRubric: () => "rubric",
    };
    const agentTask: AgentTaskInterface = createAgentTask({
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
    const game: GameScenarioInterface = new GridCtfScenario();
    const artifactEditing: ArtifactEditingInterface = {
      describeTask: () => "task",
      getRubric: () => "rubric",
      initialArtifacts: () => [],
      getEditPrompt: () => "prompt",
      validateArtifact: () => ({}),
      evaluateEdits: () => ({}),
    };
    const negotiation: NegotiationInterface = {
      ...simulation,
      getHiddenPreferences: () => ({}),
      getRounds: () => [],
      getOpponentModel: () => null,
      updateOpponentModel: () => ({}),
      evaluateNegotiation: () => ({}),
    };
    const investigation: InvestigationInterface = {
      ...simulation,
      getEvidencePool: () => [],
      evaluateEvidenceChain: () => ({}),
      evaluateDiagnosis: () => ({}),
    };
    const workflow: WorkflowInterface = {
      ...simulation,
      getWorkflowSteps: () => [],
      executeStep: () => ({}),
      executeCompensation: () => ({}),
      getSideEffects: () => [],
      evaluateWorkflow: () => ({}),
    };
    const schemaEvolution: SchemaEvolutionInterface = {
      ...simulation,
      getMutations: () => [],
      getSchemaVersion: () => 1,
      getMutationLog: () => [],
      applyMutation: () => ({}),
      checkContextValidity: () => [],
      evaluateAdaptation: () => ({}),
    };
    const toolFragility: ToolFragilityInterface = {
      ...simulation,
      getToolContracts: () => [],
      getDriftLog: () => [],
      injectDrift: () => ({}),
      attributeFailure: () => ({}),
      evaluateFragility: () => ({}),
    };
    const operatorLoop: OperatorLoopInterface = {
      ...simulation,
      getEscalationLog: () => [],
      getClarificationLog: () => [],
      escalate: () => ({}),
      requestClarification: () => ({}),
      evaluateJudgment: () => ({}),
    };
    const coordination: CoordinationInterface = {
      ...simulation,
      getWorkerContexts: () => [],
      getHandoffLog: () => [],
      recordHandoff: () => ({}),
      mergeOutputs: () => ({}),
      evaluateCoordination: () => ({}),
    };

    expect(family).toBe("simulation");
    expect(simulation.getRubric()).toBe("rubric");
    expect(agentTask.getTaskPrompt({})).toContain("Summarize the incident.");
    expect(game.name).toBeDefined();
    expect(artifactEditing.describeTask()).toBe("task");
    expect(negotiation.getRounds({})).toEqual([]);
    expect(investigation.getEvidencePool({})).toEqual([]);
    expect(workflow.getWorkflowSteps()).toEqual([]);
    expect(schemaEvolution.getSchemaVersion({})).toBe(1);
    expect(toolFragility.getToolContracts({})).toEqual([]);
    expect(operatorLoop.getEscalationLog({})).toEqual([]);
    expect(coordination.getWorkerContexts({})).toEqual([]);
  });
});
