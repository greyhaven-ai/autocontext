import { describe, expect, it } from "vitest";

import { generateSimulationSource } from "../src/scenarios/codegen/simulation-codegen.js";
import { renderCodegenTemplate } from "../src/scenarios/codegen/template-renderer.js";

describe("template-backed simulation codegen", () => {
  it("renders placeholder templates deterministically", () => {
    const rendered = renderCodegenTemplate("const x = __VALUE__;\nconst y = __LABEL__;\n", {
      __VALUE__: "42",
      __LABEL__: JSON.stringify("demo"),
    });

    expect(rendered).toContain("const x = 42;");
    expect(rendered).toContain('const y = "demo";');
    expect(rendered).not.toContain("__VALUE__");
    expect(rendered).not.toContain("__LABEL__");
  });

  it("generates simulation code with all placeholders resolved", () => {
    const source = generateSimulationSource(
      {
        description: "Deploy service",
        environment_description: "Cloud env",
        initial_state_description: "Nothing deployed",
        success_criteria: ["service deployed"],
        failure_modes: ["timeout"],
        max_steps: 5,
        actions: [
          { name: "provision", description: "Provision", parameters: {}, preconditions: [], effects: ["infra_ready"] },
        ],
      },
      "template_sim",
    );

    expect(source).toContain("template_sim");
    expect(source).not.toMatch(/__[A-Z0-9_]+__/);
    expect(() => new Function(source)).not.toThrow();
  });
});
