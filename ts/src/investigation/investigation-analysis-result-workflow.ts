import { join } from "node:path";

import type { InvestigationRequest, InvestigationResult } from "./investigation-contracts.js";
import type { InvestigationExecutionResult } from "./investigation-execution-workflow.js";
import { executeGeneratedInvestigation } from "./investigation-execution-workflow.js";
import type { InvestigationHypothesisSet } from "./investigation-generation-workflow.js";
import { generateInvestigationHypotheses } from "./investigation-generation-workflow.js";
import {
  buildInvestigationConclusion,
  buildInvestigationEvidence,
  evaluateInvestigationHypotheses,
  identifyInvestigationUnknowns,
  recommendInvestigationNextSteps,
} from "./investigation-analysis-workflow.js";
import { buildCompletedInvestigationResult, persistInvestigationReport } from "./investigation-result-workflow.js";
import type { LLMProvider } from "../types/index.js";

export interface InvestigationAnalysisResultRequest {
  id: string;
  name: string;
  request: InvestigationRequest;
  provider: LLMProvider;
  source: string;
  healedSpec: Record<string, unknown>;
  investigationDir: string;
}

export interface InvestigationAnalysisResultDependencies {
  executeGeneratedInvestigation: typeof executeGeneratedInvestigation;
  generateInvestigationHypotheses: typeof generateInvestigationHypotheses;
  buildInvestigationEvidence: typeof buildInvestigationEvidence;
  evaluateInvestigationHypotheses: typeof evaluateInvestigationHypotheses;
  buildInvestigationConclusion: typeof buildInvestigationConclusion;
  identifyInvestigationUnknowns: typeof identifyInvestigationUnknowns;
  recommendInvestigationNextSteps: typeof recommendInvestigationNextSteps;
  buildCompletedInvestigationResult: typeof buildCompletedInvestigationResult;
  persistInvestigationReport: typeof persistInvestigationReport;
}

export async function executeInvestigationAnalysisResult(
  opts: InvestigationAnalysisResultRequest,
  dependencies: InvestigationAnalysisResultDependencies,
): Promise<InvestigationResult> {
  const execution = await dependencies.executeGeneratedInvestigation({
    source: opts.source,
    maxSteps: opts.request.maxSteps,
  });
  const hypothesisData = await dependencies.generateInvestigationHypotheses({
    provider: opts.provider,
    description: opts.request.description,
    execution,
    maxHypotheses: opts.request.maxHypotheses,
  });

  const evidence = dependencies.buildInvestigationEvidence(execution);
  const { evidence: annotatedEvidence, hypotheses } = dependencies.evaluateInvestigationHypotheses(
    hypothesisData,
    evidence,
    opts.healedSpec,
  );

  const conclusion = dependencies.buildInvestigationConclusion(hypotheses, annotatedEvidence);
  const unknowns = dependencies.identifyInvestigationUnknowns(hypotheses, annotatedEvidence);
  const nextSteps = dependencies.recommendInvestigationNextSteps(hypotheses, unknowns);
  const reportPath = join(opts.investigationDir, "report.json");

  const result = dependencies.buildCompletedInvestigationResult({
    id: opts.id,
    name: opts.name,
    description: opts.request.description,
    question: hypothesisData.question,
    hypotheses,
    evidence: annotatedEvidence,
    conclusion,
    unknowns,
    recommendedNextSteps: nextSteps,
    stepsExecuted: execution.stepsExecuted,
    investigationDir: opts.investigationDir,
    reportPath,
  });
  dependencies.persistInvestigationReport(reportPath, result);
  return result;
}
