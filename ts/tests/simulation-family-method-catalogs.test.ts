import { describe, expect, it } from "vitest";

import {
  COORDINATION_METHOD_VARIANTS,
  INVESTIGATION_METHOD_VARIANTS,
  matchesSimulationFamilyContract,
  NEGOTIATION_METHOD_VARIANTS,
  OPERATOR_LOOP_METHOD_VARIANTS,
} from "../src/scenarios/simulation-family-method-catalogs.js";

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

describe("simulation family method catalogs", () => {
  it("exposes reusable method-variant catalogs for simulation-derived families", () => {
    expect(NEGOTIATION_METHOD_VARIANTS).toContainEqual([
      "getHiddenPreferences",
      "get_hidden_preferences",
    ]);
    expect(INVESTIGATION_METHOD_VARIANTS).toContainEqual([
      "getEvidencePool",
      "get_evidence_pool",
    ]);
    expect(COORDINATION_METHOD_VARIANTS).toContainEqual([
      "mergeOutputs",
      "merge_outputs",
    ]);
    expect(OPERATOR_LOOP_METHOD_VARIANTS).toContain("escalate");
  });

  it("matches simulation family contracts through the shared catalog helper", () => {
    expect(
      matchesSimulationFamilyContract(
        {
          ...simulationBase,
          getEvidencePool: () => [],
          evaluateEvidenceChain: () => 0.5,
          evaluateDiagnosis: () => ({}),
        },
        INVESTIGATION_METHOD_VARIANTS,
      ),
    ).toBe(true);

    expect(
      matchesSimulationFamilyContract(
        {
          ...simulationBase,
          getWorkerContexts: () => [],
          getHandoffLog: () => [],
        },
        COORDINATION_METHOD_VARIANTS,
      ),
    ).toBe(false);
  });
});
