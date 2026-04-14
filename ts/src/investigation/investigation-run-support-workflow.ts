import { generateScenarioSource } from "../scenarios/codegen/registry.js";
import { validateGeneratedScenario } from "../scenarios/codegen/execution-validator.js";
import { healSpec as defaultHealSpec } from "../scenarios/spec-auto-heal.js";
import type { InvestigationRequest, InvestigationResult } from "./investigation-contracts.js";
import { executeGeneratedInvestigation } from "./investigation-execution-workflow.js";
import {
  buildInvestigationSpec,
  generateInvestigationHypotheses,
} from "./investigation-generation-workflow.js";
import {
  buildFailedInvestigationResult,
  persistInvestigationArtifacts,
} from "./investigation-engine-helpers.js";
import type { InvestigationScenarioPreparationDependencies } from "./investigation-scenario-preparation-workflow.js";
import {
  buildInvestigationConclusion,
  buildInvestigationEvidence,
  evaluateInvestigationHypotheses,
  identifyInvestigationUnknowns,
  recommendInvestigationNextSteps,
} from "./investigation-analysis-workflow.js";
import {
  buildCompletedInvestigationResult,
  persistInvestigationReport,
} from "./investigation-result-workflow.js";

export interface InvestigationRunDependencies extends InvestigationScenarioPreparationDependencies {
  executeGeneratedInvestigation: typeof executeGeneratedInvestigation;
  generateInvestigationHypotheses: typeof generateInvestigationHypotheses;
  buildInvestigationEvidence: typeof buildInvestigationEvidence;
  evaluateInvestigationHypotheses: typeof evaluateInvestigationHypotheses;
  buildInvestigationConclusion: typeof buildInvestigationConclusion;
  identifyInvestigationUnknowns: typeof identifyInvestigationUnknowns;
  recommendInvestigationNextSteps: typeof recommendInvestigationNextSteps;
  buildCompletedInvestigationResult: typeof buildCompletedInvestigationResult;
  persistInvestigationReport: typeof persistInvestigationReport;
  buildFailedInvestigationResult: typeof buildFailedInvestigationResult;
}

export const DEFAULT_INVESTIGATION_RUN_DEPENDENCIES: InvestigationRunDependencies = {
  buildInvestigationSpec,
  healSpec: defaultHealSpec,
  generateScenarioSource,
  validateGeneratedScenario,
  persistInvestigationArtifacts,
  executeGeneratedInvestigation,
  generateInvestigationHypotheses,
  buildInvestigationEvidence,
  evaluateInvestigationHypotheses,
  buildInvestigationConclusion,
  identifyInvestigationUnknowns,
  recommendInvestigationNextSteps,
  buildCompletedInvestigationResult,
  persistInvestigationReport,
  buildFailedInvestigationResult,
};

export function resolveInvestigationRunDependencies(
  overrides: Partial<InvestigationRunDependencies> = {},
): InvestigationRunDependencies {
  return {
    ...DEFAULT_INVESTIGATION_RUN_DEPENDENCIES,
    ...overrides,
  };
}

export function buildFailedInvestigationRunResult(opts: {
  id: string;
  name: string;
  request: InvestigationRequest;
  errors: string[];
  dependencies: Pick<InvestigationRunDependencies, "buildFailedInvestigationResult">;
}): InvestigationResult {
  return opts.dependencies.buildFailedInvestigationResult(
    opts.id,
    opts.name,
    opts.request,
    opts.errors,
  );
}
