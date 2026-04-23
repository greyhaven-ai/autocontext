import type { InvestigationBrowserContext } from "./browser-context.js";

export interface InvestigationRequest {
  description: string;
  maxSteps?: number;
  maxHypotheses?: number;
  saveAs?: string;
  strictEvidence?: boolean;
  browserContext?: InvestigationBrowserContext;
}

export interface Hypothesis {
  id: string;
  statement: string;
  status: "supported" | "contradicted" | "unresolved";
  confidence: number;
}

export interface Evidence {
  id: string;
  kind: string;
  source: string;
  summary: string;
  supports: string[];
  contradicts: string[];
  isRedHerring: boolean;
}

export interface Conclusion {
  bestExplanation: string;
  confidence: number;
  limitations: string[];
}

export interface InvestigationResult {
  id: string;
  name: string;
  family: "investigation";
  status: "completed" | "failed";
  description: string;
  question: string;
  hypotheses: Hypothesis[];
  evidence: Evidence[];
  conclusion: Conclusion;
  unknowns: string[];
  recommendedNextSteps: string[];
  stepsExecuted: number;
  artifacts: {
    investigationDir: string;
    reportPath?: string;
  };
  error?: string;
}
