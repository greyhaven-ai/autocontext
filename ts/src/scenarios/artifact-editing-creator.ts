import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { LLMProvider } from "../types/index.js";
import type { ArtifactEditingSpec } from "./artifact-editing-spec.js";
import { designArtifactEditing } from "./artifact-editing-designer.js";
import { validateForFamily } from "./family-pipeline.js";
import { getScenarioTypeMarker } from "./families.js";

export interface ArtifactEditingCreatorOpts {
  provider: LLMProvider;
  model?: string;
  knowledgeRoot: string;
}

export interface ArtifactEditingScenarioHandle {
  family: "artifact_editing";
  name: string;
  spec: ArtifactEditingSpec;
}

function className(name: string): string {
  return name
    .split(/[^a-zA-Z0-9]+/)
    .filter(Boolean)
    .map((part) => part[0]!.toUpperCase() + part.slice(1))
    .join("") + "ArtifactEditing";
}

function generateScenarioSource(spec: ArtifactEditingSpec, name: string): string {
  const artifacts = spec.artifacts
    .map((artifact) => `            Artifact(path=${JSON.stringify(artifact.path)}, content=${JSON.stringify(artifact.content)}, content_type=${JSON.stringify(artifact.contentType)}, metadata=${JSON.stringify(artifact.metadata)})`)
    .join(",\n");
  return `from __future__ import annotations

import json
import re

from autocontext.scenarios.artifact_editing import Artifact, ArtifactEditingInterface, ArtifactEditingResult, ArtifactValidationResult


class ${className(name)}(ArtifactEditingInterface):
    name = ${JSON.stringify(name)}
    _validation_rules = ${JSON.stringify(spec.validationRules)}

    def describe_task(self) -> str:
        return ${JSON.stringify(spec.taskDescription)}

    def get_rubric(self) -> str:
        return ${JSON.stringify(spec.rubric)}

    def initial_artifacts(self, seed: int | None = None) -> list[Artifact]:
        return [
${artifacts}
        ]

    def get_edit_prompt(self, artifacts: list[Artifact]) -> str:
        rendered = json.dumps([artifact.to_dict() for artifact in artifacts], indent=2)
        rules = "\\n".join(f"- {rule}" for rule in self._validation_rules)
        return (
            f"{self.describe_task()}\\n\\n"
            f"Artifacts:\\n{rendered}\\n\\n"
            f"Validation rules:\\n{rules}\\n\\n"
            'Return JSON with shape {"artifacts": [{"path": "...", "content": "...", "content_type": "..."}]} containing the full edited artifact set.'
        )

    def _rules_for_path(self, path: str) -> list[str]:
        relevant: list[str] = []
        for rule in self._validation_rules:
            if " must " in rule:
                prefix, _ = rule.split(" must ", 1)
                if "/" in prefix and prefix.strip() != path:
                    continue
            relevant.append(rule)
        return relevant

    def _extract_snippets(self, rule: str) -> list[str]:
        return [match[0] or match[1] for match in re.findall(r'"([^"]+)"|\\'([^\\']+)\\'', rule)]

    def validate_artifact(self, artifact: Artifact) -> ArtifactValidationResult:
        errors: list[str] = []
        warnings: list[str] = []
        if not artifact.content.strip():
            errors.append(f"{artifact.path} must not be empty")
        for rule in self._rules_for_path(artifact.path):
            snippets = self._extract_snippets(rule)
            if not snippets:
                continue
            if "must not contain" in rule:
                for snippet in snippets:
                    if snippet in artifact.content:
                        errors.append(f"{artifact.path} violates rule: {rule}")
            else:
                for snippet in snippets:
                    if snippet not in artifact.content:
                        errors.append(f"{artifact.path} violates rule: {rule}")
        return ArtifactValidationResult(valid=not errors, errors=errors, warnings=warnings)

    def evaluate_edits(self, original: list[Artifact], edited: list[Artifact]) -> ArtifactEditingResult:
        diffs = self.compute_diffs(original, edited)
        validations = [self.validate_artifact(artifact) for artifact in edited]
        valid_count = sum(1 for result in validations if result.valid)
        error_count = sum(len(result.errors) for result in validations)
        correctness = valid_count / max(len(edited), 1)
        change_score = 1.0 if diffs else 0.0
        baseline = max(len(original), 1)
        precision = 1.0 if len(diffs) <= baseline else max(0.2, 1.0 - ((len(diffs) - baseline) / baseline) * 0.2)
        score = round((correctness * 0.7) + (change_score * 0.15) + (precision * 0.15), 4)
        return ArtifactEditingResult(
            score=score,
            reasoning=f"Validated {valid_count} of {len(edited)} artifacts with {len(diffs)} tracked edits.",
            dimension_scores={"correctness": round(correctness, 4), "change_completeness": round(change_score, 4), "precision": round(precision, 4)},
            diffs=diffs,
            validation=ArtifactValidationResult(
                valid=error_count == 0,
                errors=[error for result in validations for error in result.errors],
                warnings=[warning for result in validations for warning in result.warnings],
            ),
            artifacts_modified=len(diffs),
            artifacts_valid=valid_count,
        )
`;
}

export class ArtifactEditingCreator {
  private provider: LLMProvider;
  private model: string;
  private knowledgeRoot: string;

  constructor(opts: ArtifactEditingCreatorOpts) {
    this.provider = opts.provider;
    this.model = opts.model ?? opts.provider.defaultModel();
    this.knowledgeRoot = opts.knowledgeRoot;
  }

  async create(description: string, name: string): Promise<ArtifactEditingScenarioHandle> {
    const llmFn = async (system: string, user: string): Promise<string> => {
      const result = await this.provider.complete({
        systemPrompt: system,
        userPrompt: user,
        model: this.model,
      });
      return result.text;
    };
    const spec = await designArtifactEditing(description, llmFn);
    const errors = validateForFamily("artifact_editing", spec);
    if (errors.length > 0) {
      throw new Error(`artifact-editing spec validation failed: ${errors.join("; ")}`);
    }

    const customDir = join(this.knowledgeRoot, "_custom_scenarios");
    const scenarioDir = join(customDir, name);
    if (!existsSync(scenarioDir)) mkdirSync(scenarioDir, { recursive: true });

    writeFileSync(join(scenarioDir, "scenario.py"), generateScenarioSource(spec, name), "utf-8");
    writeFileSync(join(scenarioDir, "scenario_type.txt"), getScenarioTypeMarker("artifact_editing"), "utf-8");
    writeFileSync(
      join(scenarioDir, "spec.json"),
      JSON.stringify(
        {
          name,
          scenario_type: getScenarioTypeMarker("artifact_editing"),
          task_description: spec.taskDescription,
          rubric: spec.rubric,
          validation_rules: spec.validationRules,
          artifacts: spec.artifacts.map((artifact) => ({
            path: artifact.path,
            content: artifact.content,
            content_type: artifact.contentType,
            metadata: artifact.metadata,
          })),
        },
        null,
        2,
      ),
      "utf-8",
    );

    return { family: "artifact_editing", name, spec };
  }
}
