import type { LLMProvider } from "../types/index.js";
import {
  buildFallbackInvestigationHypothesisSet,
  parseInvestigationHypothesisResponse,
  parseInvestigationSpecResponse,
} from "./investigation-generation-parsing.js";
import {
  buildInvestigationHypothesisPrompt,
  buildInvestigationSpecPrompt,
} from "./investigation-generation-prompts.js";

export interface InvestigationHypothesisDraft {
  statement: string;
  confidence: number;
}

export interface InvestigationHypothesisSet {
  hypotheses: InvestigationHypothesisDraft[];
  question: string;
}

export async function buildInvestigationSpec(opts: {
  provider: LLMProvider;
  description: string;
}): Promise<Record<string, unknown>> {
  const result = await opts.provider.complete(
    buildInvestigationSpecPrompt(opts.description),
  );

  const parsed = parseInvestigationSpecResponse(result.text);
  if (!parsed) {
    throw new Error("Investigation spec generation did not return valid JSON");
  }
  return parsed;
}

export async function generateInvestigationHypotheses(opts: {
  provider: LLMProvider;
  description: string;
  execution: { stepsExecuted: number; collectedEvidence: Array<{ content: string }> };
  maxHypotheses?: number;
}): Promise<InvestigationHypothesisSet> {
  try {
    const result = await opts.provider.complete(
      buildInvestigationHypothesisPrompt({
        description: opts.description,
        execution: opts.execution,
        maxHypotheses: opts.maxHypotheses,
      }),
    );

    const parsed = parseInvestigationHypothesisResponse({
      text: result.text,
      description: opts.description,
      maxHypotheses: opts.maxHypotheses,
    });
    if (parsed) {
      return parsed;
    }
  } catch {
    // fallback
  }

  return buildFallbackInvestigationHypothesisSet({
    description: opts.description,
    maxHypotheses: opts.maxHypotheses,
  });
}
