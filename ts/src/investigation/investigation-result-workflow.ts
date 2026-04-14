import { writeFileSync } from "node:fs";

import type {
  Conclusion,
  Evidence,
  Hypothesis,
  InvestigationResult,
} from "./investigation-contracts.js";

export function buildCompletedInvestigationResult(opts: {
  id: string;
  name: string;
  description: string;
  question: string | undefined;
  hypotheses: Hypothesis[];
  evidence: Evidence[];
  conclusion: Conclusion;
  unknowns: string[];
  recommendedNextSteps: string[];
  stepsExecuted: number;
  investigationDir: string;
  reportPath: string;
}): InvestigationResult {
  return {
    id: opts.id,
    name: opts.name,
    family: "investigation",
    status: "completed",
    description: opts.description,
    question: String(opts.question ?? `What caused: ${opts.description}`),
    hypotheses: opts.hypotheses,
    evidence: opts.evidence,
    conclusion: opts.conclusion,
    unknowns: opts.unknowns,
    recommendedNextSteps: opts.recommendedNextSteps,
    stepsExecuted: opts.stepsExecuted,
    artifacts: {
      investigationDir: opts.investigationDir,
      reportPath: opts.reportPath,
    },
  };
}

export function persistInvestigationReport(
  reportPath: string,
  result: InvestigationResult,
): void {
  writeFileSync(reportPath, JSON.stringify(result, null, 2), "utf-8");
}
