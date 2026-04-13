import { describe, expect, it } from "vitest";

import {
  assertFamilyContractWithCatalog,
  detectFamilyWithDetectors,
} from "../src/scenarios/family-assertion-workflow.js";

describe("family assertion workflow", () => {
  it("asserts contracts through a supplied guard catalog", () => {
    expect(() =>
      assertFamilyContractWithCatalog({
        obj: {},
        family: "coordination",
        context: "test scenario",
        guards: {
          game: () => false,
          agent_task: () => false,
          simulation: () => false,
          negotiation: () => false,
          investigation: () => false,
          workflow: () => false,
          schema_evolution: () => false,
          tool_fragility: () => false,
          operator_loop: () => false,
          coordination: () => false,
          artifact_editing: () => false,
        },
        expectedMethods: {
          game: ["play"],
          agent_task: ["evaluate"],
          simulation: ["executeAction"],
          negotiation: ["evaluateNegotiation"],
          investigation: ["evaluateDiagnosis"],
          workflow: ["evaluateWorkflow"],
          schema_evolution: ["getMutations"],
          tool_fragility: ["injectDrift"],
          operator_loop: ["escalate"],
          coordination: ["recordHandoff", "mergeOutputs"],
          artifact_editing: ["evaluateEdits"],
        },
      }),
    ).toThrow("test scenario does not satisfy 'coordination' contract. Expected methods: recordHandoff, mergeOutputs");
  });

  it("detects families through ordered detectors", () => {
    expect(
      detectFamilyWithDetectors(
        { kind: "coordination" },
        [
          ["coordination", (obj) => (obj as { kind?: string }).kind === "coordination"],
          ["simulation", () => true],
        ],
      ),
    ).toBe("coordination");
  });
});
