import { describe, expect, it } from "vitest";

import { generateInvestigationSource } from "../src/scenarios/codegen/investigation-codegen.js";
import { INVESTIGATION_SCENARIO_TEMPLATE } from "../src/scenarios/codegen/templates/investigation-template.js";

describe("template-backed investigation codegen", () => {
  it("exposes a reusable investigation template", () => {
    expect(INVESTIGATION_SCENARIO_TEMPLATE).toContain("module.exports = { scenario }");
    expect(INVESTIGATION_SCENARIO_TEMPLATE).toContain("__SCENARIO_NAME__");
  });

  it("generates investigation code with all placeholders resolved", () => {
    const source = generateInvestigationSource(
      {
        description: "Debug crash",
        environment_description: "Production logs and traces",
        initial_state_description: "No evidence collected",
        success_criteria: ["correct diagnosis"],
        failure_modes: ["red herring accepted"],
        max_steps: 8,
        evidence_pool: [
          { id: "log1", content: "null pointer trace", isRedHerring: false, relevance: 0.9 },
        ],
        correct_diagnosis: "null pointer",
        actions: [
          { name: "check_logs", description: "Check logs", parameters: {}, preconditions: [], effects: [] },
        ],
      },
      "debug_crash",
    );

    expect(source).toContain("debug_crash");
    expect(source).toContain("evaluateDiagnosis");
    expect(source).not.toMatch(/__[A-Z0-9_]+__/);
    expect(() => new Function(source)).not.toThrow();
  });
});
