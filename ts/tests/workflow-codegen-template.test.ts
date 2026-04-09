import { describe, expect, it } from "vitest";

import { generateWorkflowSource } from "../src/scenarios/codegen/workflow-codegen.js";
import { WORKFLOW_SCENARIO_TEMPLATE } from "../src/scenarios/codegen/templates/workflow-template.js";

describe("template-backed workflow codegen", () => {
  it("exposes a reusable workflow template", () => {
    expect(WORKFLOW_SCENARIO_TEMPLATE).toContain("module.exports = { scenario }");
    expect(WORKFLOW_SCENARIO_TEMPLATE).toContain("__SCENARIO_NAME__");
  });

  it("generates workflow code with all placeholders resolved", () => {
    const source = generateWorkflowSource(
      {
        description: "Payment flow",
        environment_description: "Checkout pipeline",
        initial_state_description: "No steps completed",
        success_criteria: ["payment settled"],
        failure_modes: ["rollback required"],
        max_steps: 7,
        steps: [
          {
            name: "validate",
            description: "Validate request",
            compensationAction: "rollback",
            sideEffects: ["validation_logged"],
            retryable: true,
          },
        ],
        actions: [
          {
            name: "validate",
            description: "Validate request",
            parameters: {},
            preconditions: [],
            effects: ["validated"],
          },
        ],
      },
      "payment_flow",
    );

    expect(source).toContain("payment_flow");
    expect(source).toContain("executeCompensation");
    expect(source).not.toMatch(/__[A-Z0-9_]+__/);
    expect(() => new Function(source)).not.toThrow();
  });
});
