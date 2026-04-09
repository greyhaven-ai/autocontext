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

  it("preserves placeholder-like text inside artifact-editing fields", () => {
    const source = generateArtifactEditingSource(
      {
        description: "__EDIT_INSTRUCTIONS__ desc",
        rubric: "Check validity",
        edit_instructions: "Follow steps",
        artifacts: [
          {
            name: "config.yaml",
            content: "kind: ConfigMap",
            format: "yaml",
            validationRules: ["kind"],
          },
        ],
      },
      "edit_config",
    );

    expect(source).toContain('return "__EDIT_INSTRUCTIONS__ desc";');
    expect(source).not.toContain('return ""Follow steps" desc";');
  });

  it("does not reject placeholder-like artifact-editing data from user specs", () => {
    expect(() =>
      generateArtifactEditingSource(
        {
          description: "Edit config",
          rubric: "__SAFE_MODE__",
          edit_instructions: "Follow steps",
          artifacts: [
            {
              name: "config.yaml",
              content: "__SCENARIO_NAME__ content",
              format: "yaml",
              validationRules: [],
            },
          ],
        },
        "edit_config",
      ),
    ).not.toThrow();
  });
});
