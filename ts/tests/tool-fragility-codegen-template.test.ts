import { describe, expect, it } from "vitest";

import { generateToolFragilitySource } from "../src/scenarios/codegen/tool-fragility-codegen.js";
import { TOOL_FRAGILITY_SCENARIO_TEMPLATE } from "../src/scenarios/codegen/templates/tool-fragility-template.js";

describe("template-backed tool-fragility codegen", () => {
  it("exposes a reusable tool-fragility template", () => {
    expect(TOOL_FRAGILITY_SCENARIO_TEMPLATE).toContain("module.exports = { scenario }");
    expect(TOOL_FRAGILITY_SCENARIO_TEMPLATE).toContain("__SCENARIO_NAME__");
  });

  it("generates tool-fragility code with all placeholders resolved", () => {
    const source = generateToolFragilitySource(
      {
        description: "API drift test",
        environment_description: "External API surface",
        initial_state_description: "No drift injected",
        success_criteria: ["drift detected"],
        failure_modes: ["drift missed"],
        max_steps: 4,
        tool_contracts: [
          { toolName: "api_call", expectedBehavior: "200 OK", driftBehavior: "timeout" },
        ],
        actions: [
          { name: "api_call", description: "Call API", parameters: {}, preconditions: [], effects: [] },
        ],
      },
      "api_drift",
    );

    expect(source).toContain("api_drift");
    expect(source).toContain("injectDrift");
    expect(source).not.toMatch(/__[A-Z0-9_]+__/);
    expect(() => new Function(source)).not.toThrow();
  });
});
