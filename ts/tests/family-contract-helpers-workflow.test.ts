import { describe, expect, it } from "vitest";

import {
  formatExpectedMethods,
  hasMethodVariants,
  hasSimulationMethodVariants,
} from "../src/scenarios/family-contract-helpers.js";
import { EXPECTED_METHODS } from "../src/scenarios/family-expected-methods.js";

describe("family contract helper workflow", () => {
  it("matches exact and variant method names", () => {
    const candidate = {
      describe_scenario() {
        return "scenario";
      },
      describe_environment() {
        return {};
      },
      initial_state() {
        return {};
      },
      get_available_actions() {
        return [];
      },
      execute_action() {
        return [{}, {}];
      },
      is_terminal() {
        return false;
      },
      evaluate_trace() {
        return {};
      },
      get_rubric() {
        return "rubric";
      },
      get_hidden_preferences() {
        return {};
      },
    };

    expect(hasMethodVariants(candidate, ["describeScenario", "describe_scenario"], ["getRubric", "get_rubric"])).toBe(true);
    expect(hasSimulationMethodVariants(candidate, ["getHiddenPreferences", "get_hidden_preferences"])).toBe(true);
  });

  it("formats expected methods and exposes family method catalogs", () => {
    expect(formatExpectedMethods(EXPECTED_METHODS.coordination)).toContain("getWorkerContexts");
    expect(EXPECTED_METHODS.artifact_editing).toContain("evaluateEdits");
    expect(EXPECTED_METHODS.negotiation).toContain("evaluateNegotiation");
  });
});
