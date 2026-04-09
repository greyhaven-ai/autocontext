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

  it("preserves placeholder-like text inside investigation fields", () => {
    const source = generateInvestigationSource(
      {
        description: "__MAX_STEPS__ desc",
        environment_description: "Production logs and traces",
        initial_state_description: "No evidence collected",
        success_criteria: ["correct diagnosis"],
        failure_modes: ["red herring accepted"],
        max_steps: 8,
        evidence_pool: [
          { id: "log1", content: "__CORRECT_DIAGNOSIS__ clue", isRedHerring: false, relevance: 0.9 },
        ],
        correct_diagnosis: "null pointer",
        actions: [
          { name: "check_logs", description: "Check logs", parameters: {}, preconditions: [], effects: [] },
        ],
      },
      "debug_crash",
    );

    expect(source).toContain('return "__MAX_STEPS__ desc";');
    expect(source).not.toContain('return "8 desc";');
    expect(source).toContain('"content": "__CORRECT_DIAGNOSIS__ clue"');
    expect(source).not.toContain('"content": ""null pointer" clue"');
  });

  it("does not reject placeholder-like investigation data from user specs", () => {
    expect(() =>
      generateInvestigationSource(
        {
          description: "Debug crash",
          environment_description: "Production logs and traces",
          initial_state_description: "No evidence collected",
          success_criteria: ["correct diagnosis"],
          failure_modes: ["__SAFE_MODE__"],
          max_steps: 8,
          evidence_pool: [
            { id: "log1", content: "null pointer trace", isRedHerring: false, relevance: 0.9 },
          ],
          correct_diagnosis: "null pointer",
          actions: [],
        },
        "debug_crash",
      ),
    ).not.toThrow();
  });
});
