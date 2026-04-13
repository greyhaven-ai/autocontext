import { describe, expect, it } from "vitest";

import {
  assertFamilyContract,
  detectFamily,
} from "../src/scenarios/family-interface-runtime.js";

describe("family interface runtime", () => {
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

  it("detects runtime family membership", () => {
    const coordination = {
      ...simulationBase,
      getWorkerContexts: () => [],
      getHandoffLog: () => [],
      recordHandoff: () => ({}),
      mergeOutputs: () => ({}),
      evaluateCoordination: () => ({}),
    };

    expect(detectFamily(simulationBase)).toBe("simulation");
    expect(detectFamily(coordination)).toBe("coordination");
  });

  it("throws helpful assertion errors for mismatched families", () => {
    expect(() => assertFamilyContract(simulationBase, "coordination", "test scenario")).toThrow(
      /test scenario does not satisfy 'coordination' contract/i,
    );
    expect(() => assertFamilyContract({}, "schema_evolution")).toThrow(/getMutations/i);
  });
});
