import {
  normalizePositiveInteger,
  parseInvestigationJson,
} from "./investigation-engine-helpers.js";

function normalizeInvestigationConfidence(confidence: unknown): number {
  return typeof confidence === "number"
    ? Math.min(1, Math.max(0, confidence))
    : 0.5;
}

export function parseInvestigationSpecResponse(text: string): Record<string, unknown> | null {
  return parseInvestigationJson(text);
}

export function parseInvestigationHypothesisResponse(opts: {
  text: string;
  description: string;
  maxHypotheses?: number;
}): {
  question: string;
  hypotheses: Array<{ statement: string; confidence: number }>;
} | null {
  const parsed = parseInvestigationJson(opts.text);
  if (!parsed?.hypotheses || !Array.isArray(parsed.hypotheses)) {
    return null;
  }

  const hypotheses = (parsed.hypotheses as Array<Record<string, unknown>>)
    .filter((hypothesis) => typeof hypothesis.statement === "string")
    .map((hypothesis) => ({
      statement: String(hypothesis.statement),
      confidence: normalizeInvestigationConfidence(hypothesis.confidence),
    }));
  const limit = normalizePositiveInteger(opts.maxHypotheses);

  return {
    question: String(parsed.question ?? opts.description),
    hypotheses: typeof limit === "number" ? hypotheses.slice(0, limit) : hypotheses,
  };
}

export function buildFallbackInvestigationHypothesisSet(opts: {
  description: string;
  maxHypotheses?: number;
}): {
  question: string;
  hypotheses: Array<{ statement: string; confidence: number }>;
} {
  return {
    question: opts.description,
    hypotheses: [{ statement: `Investigate: ${opts.description}`, confidence: 0.5 }]
      .slice(0, normalizePositiveInteger(opts.maxHypotheses) ?? 1),
  };
}
