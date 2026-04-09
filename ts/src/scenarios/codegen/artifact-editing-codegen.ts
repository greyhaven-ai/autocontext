/**
 * Artifact-editing family codegen (AC-436).
 * Mirrors Python's autocontext/scenarios/custom/artifact_editing_codegen.py.
 */

import { renderCodegenTemplate } from "./template-renderer.js";
import { ARTIFACT_EDITING_SCENARIO_TEMPLATE } from "./templates/artifact-editing-template.js";

export function generateArtifactEditingSource(
  spec: Record<string, unknown>,
  name: string,
): string {
  const description = String(spec.description ?? "");
  const rubric = String(spec.rubric ?? spec.judgeRubric ?? "");
  const artifacts = (spec.artifacts ?? spec.initial_artifacts ?? []) as Array<{
    name: string;
    content: string;
    format: string;
    validationRules?: string[];
  }>;
  const editInstructions = String(
    spec.edit_instructions ?? spec.editInstructions ?? "Edit the artifacts according to the task.",
  );

  const artifactsJson = JSON.stringify(
    artifacts.map((artifact) => ({
      name: artifact.name,
      content: artifact.content,
      format: artifact.format ?? "text",
      validationRules: artifact.validationRules ?? [],
    })),
    null,
    2,
  );

  return renderCodegenTemplate(ARTIFACT_EDITING_SCENARIO_TEMPLATE, {
    __SCENARIO_NAME_COMMENT__: name,
    __SCENARIO_NAME__: JSON.stringify(name),
    __DESCRIPTION__: JSON.stringify(description),
    __RUBRIC__: JSON.stringify(rubric),
    __ARTIFACTS__: artifactsJson,
    __EDIT_INSTRUCTIONS__: JSON.stringify(editInstructions),
  });
}
