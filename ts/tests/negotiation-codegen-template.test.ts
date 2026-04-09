import { describe, expect, it } from "vitest";

import { generateNegotiationSource } from "../src/scenarios/codegen/negotiation-codegen.js";
import { NEGOTIATION_SCENARIO_TEMPLATE } from "../src/scenarios/codegen/templates/negotiation-template.js";

describe("template-backed negotiation codegen", () => {
  it("exposes a reusable negotiation template", () => {
    expect(NEGOTIATION_SCENARIO_TEMPLATE).toContain("module.exports = { scenario }");
    expect(NEGOTIATION_SCENARIO_TEMPLATE).toContain("__SCENARIO_NAME__");
  });

  it("generates negotiation code with all placeholders resolved", () => {
    const source = generateNegotiationSource(
      {
        description: "Price negotiation",
        environment_description: "Marketplace haggling",
        initial_state_description: "No offers exchanged",
        success_criteria: ["agreement reached"],
        failure_modes: ["stalled negotiation"],
        max_steps: 6,
        hidden_preferences: { minPrice: 100 },
        rounds: 3,
        actions: [
          { name: "offer", description: "Make offer", parameters: {}, preconditions: [], effects: [] },
        ],
      },
      "price_negotiation",
    );

    expect(source).toContain("price_negotiation");
    expect(source).toContain("getHiddenPreferences");
    expect(source).not.toMatch(/__[A-Z0-9_]+__/);
    expect(() => new Function(source)).not.toThrow();
  });
});
