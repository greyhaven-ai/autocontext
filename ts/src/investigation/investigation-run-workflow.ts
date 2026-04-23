import type { LLMProvider } from "../types/index.js";
import type { InvestigationRequest, InvestigationResult } from "./investigation-contracts.js";
import { executeInvestigationAnalysisResult } from "./investigation-analysis-result-workflow.js";
import {
  buildFailedInvestigationRunResult,
  resolveInvestigationRunDependencies,
  type InvestigationRunDependencies,
} from "./investigation-run-support-workflow.js";
import { prepareInvestigationScenario } from "./investigation-scenario-preparation-workflow.js";

export interface InvestigationRunRequest {
  id: string;
  name: string;
  request: InvestigationRequest;
  provider: LLMProvider;
  knowledgeRoot: string;
}


export async function executeInvestigationRun(
  opts: InvestigationRunRequest,
  overrides: Partial<InvestigationRunDependencies> = {},
): Promise<InvestigationResult> {
  const dependencies = resolveInvestigationRunDependencies(overrides);

  try {
    const preparation = await prepareInvestigationScenario(
      {
        provider: opts.provider,
        description: opts.request.description,
        knowledgeRoot: opts.knowledgeRoot,
        name: opts.name,
        browserContext: opts.request.browserContext,
      },
      dependencies,
    );
    if (preparation.status === "invalid") {
      return buildFailedInvestigationRunResult({
        id: opts.id,
        name: opts.name,
        request: opts.request,
        errors: preparation.errors,
        dependencies,
      });
    }

    const { healedSpec, source, investigationDir } = preparation;

    return executeInvestigationAnalysisResult(
      {
        id: opts.id,
        name: opts.name,
        request: opts.request,
        provider: opts.provider,
        source,
        healedSpec,
        investigationDir,
      },
      dependencies,
    );
  } catch (error) {
    return buildFailedInvestigationRunResult({
      id: opts.id,
      name: opts.name,
      request: opts.request,
      errors: [error instanceof Error ? error.message : String(error)],
      dependencies,
    });
  }
}
