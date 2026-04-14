import { generateScenarioSource } from "../scenarios/codegen/registry.js";
import { validateGeneratedScenario } from "../scenarios/codegen/execution-validator.js";
import { healSpec as defaultHealSpec } from "../scenarios/spec-auto-heal.js";
import type { LLMProvider } from "../types/index.js";
import { buildInvestigationSpec } from "./investigation-generation-workflow.js";
import { persistInvestigationArtifacts } from "./investigation-engine-helpers.js";

export interface InvestigationScenarioPreparationRequest {
  provider: LLMProvider;
  description: string;
  knowledgeRoot: string;
  name: string;
}

export interface InvestigationScenarioPreparationDependencies {
  buildInvestigationSpec: typeof buildInvestigationSpec;
  healSpec: typeof defaultHealSpec;
  generateScenarioSource: typeof generateScenarioSource;
  validateGeneratedScenario: typeof validateGeneratedScenario;
  persistInvestigationArtifacts: typeof persistInvestigationArtifacts;
}

export interface PreparedInvestigationScenario {
  status: "prepared";
  healedSpec: Record<string, unknown>;
  source: string;
  investigationDir: string;
}

export interface InvalidInvestigationScenario {
  status: "invalid";
  errors: string[];
}

export type InvestigationScenarioPreparationResult =
  | PreparedInvestigationScenario
  | InvalidInvestigationScenario;

export async function prepareInvestigationScenario(
  opts: InvestigationScenarioPreparationRequest,
  dependencies: InvestigationScenarioPreparationDependencies,
): Promise<InvestigationScenarioPreparationResult> {
  const spec = await dependencies.buildInvestigationSpec({
    provider: opts.provider,
    description: opts.description,
  });
  const healedSpec = dependencies.healSpec(spec, "investigation");
  const source = dependencies.generateScenarioSource("investigation", healedSpec, opts.name);
  const validation = await dependencies.validateGeneratedScenario(source, "investigation", opts.name);

  if (!validation.valid) {
    return {
      status: "invalid",
      errors: validation.errors,
    };
  }

  return {
    status: "prepared",
    healedSpec,
    source,
    investigationDir: dependencies.persistInvestigationArtifacts(
      opts.knowledgeRoot,
      opts.name,
      healedSpec,
      source,
    ),
  };
}
