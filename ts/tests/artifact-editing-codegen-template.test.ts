import { describe, expect, it } from "vitest";

import { generateArtifactEditingSource } from "../src/scenarios/codegen/artifact-editing-codegen.js";
import { ARTIFACT_EDITING_SCENARIO_TEMPLATE } from "../src/scenarios/codegen/templates/artifact-editing-template.js";

describe("template-backed artifact-editing codegen", () => {
  it("exposes a reusable artifact-editing template", () => {
    expect(ARTIFACT_EDITING_SCENARIO_TEMPLATE).toContain("module.exports = { scenario }");
    expect(ARTIFACT_EDITING_SCENARIO_TEMPLATE).toContain("__SCENARIO_NAME__");
  });

  it("generates artifact-editing code with all placeholders resolved", () => {
    const source = generateArtifactEditingSource(
      {
        description: "Edit config",
        rubric: "Check validity",
        edit_instructions: "Update the config and preserve required keys.",
        artifacts: [
          {
            name: "config.yaml",
            content: "apiVersion: v1\nkind: ConfigMap",
            format: "yaml",
            validationRules: ["apiVersion", "kind"],
          },
        ],
      },
      "edit_config",
    );

    expect(source).toContain("edit_config");
    expect(source).toContain("validateArtifact");
    expect(source).not.toMatch(/__[A-Z0-9_]+__/);
    expect(() => new Function(source)).not.toThrow();
  });
});
