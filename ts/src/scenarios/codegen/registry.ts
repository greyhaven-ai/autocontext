import type { ScenarioFamilyName } from "../families.js";
import { healSpec } from "../spec-auto-heal.js";
import { validateGeneratedScenario, type ExecutionValidationResult } from "./execution-validator.js";
import { CodegenUnsupportedFamilyError } from "./runtime.js";

export { CodegenUnsupportedFamilyError };

import { generateSimulationSource } from "./simulation-codegen.js";
import { generateAgentTaskSource } from "./agent-task-codegen.js";
import { generateArtifactEditingSource } from "./artifact-editing-codegen.js";
import { generateInvestigationSource } from "./investigation-codegen.js";
import { generateWorkflowSource } from "./workflow-codegen.js";
import { generateNegotiationSource } from "./negotiation-codegen.js";
import { generateSchemaEvolutionSource } from "./schema-evolution-codegen.js";
import { generateToolFragilitySource } from "./tool-fragility-codegen.js";
import { generateCoordinationSource } from "./coordination-codegen.js";
import { generateOperatorLoopSource } from "./operator-loop-codegen.js";

export type CodegenFn = (spec: Record<string, unknown>, name: string) => string;

const CODEGEN_REGISTRY: Partial<Record<ScenarioFamilyName, CodegenFn>> = {
  simulation: generateSimulationSource,
  agent_task: generateAgentTaskSource,
  artifact_editing: generateArtifactEditingSource,
  investigation: generateInvestigationSource,
  workflow: generateWorkflowSource,
  negotiation: generateNegotiationSource,
  schema_evolution: generateSchemaEvolutionSource,
  tool_fragility: generateToolFragilitySource,
  coordination: generateCoordinationSource,
  operator_loop: generateOperatorLoopSource,
};

export function generateScenarioSource(
  family: ScenarioFamilyName,
  spec: Record<string, unknown>,
  name: string,
): string {
  if (family === "game") {
    throw new CodegenUnsupportedFamilyError(family);
  }

  const codegen = CODEGEN_REGISTRY[family];
  if (!codegen) {
    throw new CodegenUnsupportedFamilyError(family);
  }

  const healedSpec = healSpec(spec, family);
  return codegen(healedSpec, name);
}

export function hasCodegen(family: string): boolean {
  return family in CODEGEN_REGISTRY;
}

export async function generateAndValidateScenarioSource(
  family: ScenarioFamilyName,
  spec: Record<string, unknown>,
  name: string,
): Promise<{ source: string; validation: ExecutionValidationResult }> {
  const source = generateScenarioSource(family, spec, name);
  const validation = await validateGeneratedScenario(source, family, name);

  if (!validation.valid) {
    throw new Error(
      `Generated ${family} scenario '${name}' failed execution validation:\n` +
      validation.errors.map((e) => `  - ${e}`).join("\n"),
    );
  }

  return { source, validation };
}
