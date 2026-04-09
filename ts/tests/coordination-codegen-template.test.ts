import { describe, expect, it } from "vitest";

import { generateCoordinationSource } from "../src/scenarios/codegen/coordination-codegen.js";
import { COORDINATION_SCENARIO_TEMPLATE } from "../src/scenarios/codegen/templates/coordination-template.js";

describe("template-backed coordination codegen", () => {
  it("exposes a reusable coordination template", () => {
    expect(COORDINATION_SCENARIO_TEMPLATE).toContain("module.exports = { scenario }");
    expect(COORDINATION_SCENARIO_TEMPLATE).toContain("__SCENARIO_NAME__");
  });

  it("generates coordination code with all placeholders resolved", () => {
    const source = generateCoordinationSource(
      {
        description: "Multi-agent coordination",
        environment_description: "Shared workspace",
        initial_state_description: "No handoffs yet",
        success_criteria: ["workers coordinate"],
        failure_modes: ["handoff dropped"],
        max_steps: 6,
        workers: [
          { id: "w1", role: "analyzer", partialContext: { focus: "logs" } },
          { id: "w2", role: "synthesizer", partialContext: { focus: "summary" } },
        ],
        actions: [
          { name: "analyze", description: "Analyze data", parameters: {}, preconditions: [], effects: [] },
        ],
      },
      "multi_agent",
    );

    expect(source).toContain("multi_agent");
    expect(source).toContain("recordHandoff");
    expect(source).toContain("mergeOutputs");
    expect(source).not.toMatch(/__[A-Z0-9_]+__/);
    expect(() => new Function(source)).not.toThrow();
  });
});
