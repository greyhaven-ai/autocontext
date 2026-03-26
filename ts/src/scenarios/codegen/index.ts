/**
 * Codegen registry — routes family names to codegen functions (AC-436).
 *
 * Each codegen function takes a family-specific spec and produces a JS source
 * string that implements the family's interface methods.
 */

export { ScenarioRuntime, CodegenUnsupportedFamilyError } from "./runtime.js";
export type { ScenarioProxy, ScenarioRuntimeOpts } from "./runtime.js";

import type { ScenarioFamilyName } from "../families.js";
import { CodegenUnsupportedFamilyError } from "./runtime.js";

import { generateSimulationSource } from "./simulation-codegen.js";
import { generateAgentTaskSource } from "./agent-task-codegen.js";
import { generateArtifactEditingSource } from "./artifact-editing-codegen.js";
import { generateInvestigationSource } from "./investigation-codegen.js";
import { generateWorkflowSource } from "./workflow-codegen.js";
import { generateNegotiationSource } from "./negotiation-codegen.js";
import { generateSchemaEvolutionSource } from "./schema-evolution-codegen.js";
import { generateToolFragilitySource } from "./tool-fragility-codegen.js";
import { generateCoordinationSource } from "./coordination-codegen.js";

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
};

/**
 * Generate executable JS source for a scenario family from its spec.
 *
 * @param family - The scenario family name
 * @param spec - The family-specific spec (already validated)
 * @param name - The scenario name
 * @returns Generated JavaScript source string
 * @throws CodegenUnsupportedFamilyError for game, operator_loop, or unknown families
 */
export function generateScenarioSource(
  family: ScenarioFamilyName,
  spec: Record<string, unknown>,
  name: string,
): string {
  if (family === "game" || family === "operator_loop") {
    throw new CodegenUnsupportedFamilyError(family);
  }

  const codegen = CODEGEN_REGISTRY[family];
  if (!codegen) {
    throw new CodegenUnsupportedFamilyError(family);
  }

  return codegen(spec, name);
}

/**
 * Check whether codegen is available for a given family.
 */
export function hasCodegen(family: string): boolean {
  return family in CODEGEN_REGISTRY;
}
