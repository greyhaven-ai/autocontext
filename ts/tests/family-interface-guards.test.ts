import { describe, expect, it } from "vitest";

import {
  isAgentTask,
  isArtifactEditing,
  isCoordination,
  isGameScenario,
  isInvestigation,
  isNegotiation,
  isOperatorLoop,
  isSchemaEvolution,
  isSimulation,
  isToolFragility,
  isWorkflow,
} from "../src/scenarios/family-interface-guards.js";

describe("family interface guards", () => {
  it("exports the public family guard surface", async () => {
    const { createAgentTask } = await import("../src/scenarios/agent-task-factory.js");
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");

    const simulation = {
      describeScenario: () => "scenario",
      describeEnvironment: () => ({}),
      initialState: () => ({}),
      getAvailableActions: () => [],
      executeAction: () => [{}, {}] as [unknown, Record<string, unknown>],
      isTerminal: () => false,
      evaluateTrace: () => ({}),
      getRubric: () => "rubric",
    };
    const coordination = {
      ...simulation,
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

    expect(isGameScenario(new GridCtfScenario())).toBe(true);
    expect(isAgentTask(agentTask)).toBe(true);
    expect(isSimulation(simulation)).toBe(true);
    expect(isCoordination(coordination)).toBe(true);
    expect(isArtifactEditing(artifactEditing)).toBe(true);
    expect(isNegotiation(simulation)).toBe(false);
    expect(isInvestigation(simulation)).toBe(false);
    expect(isWorkflow(simulation)).toBe(false);
    expect(isSchemaEvolution(simulation)).toBe(false);
    expect(isToolFragility(simulation)).toBe(false);
    expect(isOperatorLoop(simulation)).toBe(false);
  });
});
