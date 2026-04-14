import { describe, expect, it } from "vitest";

import {
  detectFamilyByCatalog,
  buildFamilyGuardCatalog,
} from "../src/scenarios/family-detection-catalog.js";
import {
  isCoordination,
  isInvestigation,
  isNegotiation,
  isOperatorLoop,
  isSchemaEvolution,
  isSimulation,
  isToolFragility,
  isWorkflow,
} from "../src/scenarios/simulation-family-contracts.js";

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

describe("simulation family contracts", () => {
  it("matches simulation-derived family guards", () => {
    expect(isSimulation(simulationBase)).toBe(true);
    expect(
      isNegotiation({
        ...simulationBase,
        getHiddenPreferences: () => ({}),
        getRounds: () => [],
        getOpponentModel: () => null,
        updateOpponentModel: () => ({}),
        evaluateNegotiation: () => ({}),
      }),
    ).toBe(true);
    expect(
      isInvestigation({
        ...simulationBase,
        getEvidencePool: () => [],
        evaluateEvidenceChain: () => 0.5,
        evaluateDiagnosis: () => ({}),
      }),
    ).toBe(true);
    expect(
      isWorkflow({
        ...simulationBase,
        getWorkflowSteps: () => [],
        executeStep: () => ({}),
        executeCompensation: () => ({}),
        getSideEffects: () => [],
        evaluateWorkflow: () => ({}),
      }),
    ).toBe(true);
    expect(
      isSchemaEvolution({
        ...simulationBase,
        getMutations: () => [],
        getSchemaVersion: () => 1,
        getMutationLog: () => [],
        applyMutation: () => ({}),
        checkContextValidity: () => [],
        evaluateAdaptation: () => ({}),
      }),
    ).toBe(true);
    expect(
      isToolFragility({
        ...simulationBase,
        getToolContracts: () => [],
        getDriftLog: () => [],
        injectDrift: () => ({}),
        attributeFailure: () => ({}),
        evaluateFragility: () => ({}),
      }),
    ).toBe(true);
    expect(
      isOperatorLoop({
        ...simulationBase,
        getEscalationLog: () => [],
        getClarificationLog: () => [],
        escalate: () => ({}),
        requestClarification: () => ({}),
        evaluateJudgment: () => ({}),
      }),
    ).toBe(true);
    expect(
      isCoordination({
        ...simulationBase,
        getWorkerContexts: () => [],
        getHandoffLog: () => [],
        recordHandoff: () => ({}),
        mergeOutputs: () => ({}),
        evaluateCoordination: () => ({}),
      }),
    ).toBe(true);
  });

  it("detects families through an ordered detector catalog", () => {
    const detectors = buildFamilyGuardCatalog({
      isGameScenario: () => false,
      isAgentTask: () => false,
      isSimulation,
      isNegotiation,
      isInvestigation,
      isWorkflow,
      isSchemaEvolution,
      isToolFragility,
      isOperatorLoop,
      isCoordination,
      isArtifactEditing: () => false,
    });

    const coordination = {
      ...simulationBase,
      getWorkerContexts: () => [],
      getHandoffLog: () => [],
      recordHandoff: () => ({}),
      mergeOutputs: () => ({}),
      evaluateCoordination: () => ({}),
    };

    expect(
      detectFamilyByCatalog(coordination, [
        ["coordination", detectors.coordination],
        ["simulation", detectors.simulation],
      ]),
    ).toBe("coordination");
  });
});
