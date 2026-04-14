import { describe, expect, it } from "vitest";

import {
  buildSimulationDerivedFamilyGuardCatalog,
  buildSimulationFamilyGuard,
} from "../src/scenarios/simulation-family-guard-builders.js";
import { INVESTIGATION_METHOD_VARIANTS } from "../src/scenarios/simulation-family-method-catalogs.js";

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

describe("simulation family guard builders", () => {
  it("builds reusable simulation-derived family guards from method variants", () => {
    const isInvestigation = buildSimulationFamilyGuard(INVESTIGATION_METHOD_VARIANTS);

    expect(
      isInvestigation({
        ...simulationBase,
        getEvidencePool: () => [],
        evaluateEvidenceChain: () => 0.5,
        evaluateDiagnosis: () => ({}),
      }),
    ).toBe(true);
    expect(
      isInvestigation({
        ...simulationBase,
        getEvidencePool: () => [],
      }),
    ).toBe(false);
  });

  it("builds the grouped simulation-derived guard catalog", () => {
    const guards = buildSimulationDerivedFamilyGuardCatalog();

    expect(guards.simulation(simulationBase)).toBe(true);
    expect(
      guards.coordination({
        ...simulationBase,
        getWorkerContexts: () => [],
        getHandoffLog: () => [],
        recordHandoff: () => ({}),
        mergeOutputs: () => ({}),
        evaluateCoordination: () => ({}),
      }),
    ).toBe(true);
    expect(guards.operatorLoop(simulationBase)).toBe(false);
  });
});
