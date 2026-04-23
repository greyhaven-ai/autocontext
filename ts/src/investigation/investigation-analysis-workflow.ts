import type { Conclusion, Evidence, Hypothesis } from "./investigation-contracts.js";
import {
  buildInvestigationBrowserEvidence,
  type InvestigationBrowserContext,
} from "./browser-context.js";

interface CollectedEvidenceItem {
  id: string;
  content: string;
  isRedHerring: boolean;
}

function normalizeText(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function tokenize(text: string): string[] {
  const stopwords = new Set([
    "a", "an", "and", "the", "to", "of", "for", "in", "on", "at",
    "by", "with", "after", "before", "from", "our", "your", "their",
    "is", "was", "were", "be", "this", "that",
  ]);

  return normalizeText(text)
    .split(" ")
    .filter((token) => token.length > 1 && !stopwords.has(token));
}

function similarityScore(left: string, right: string): number {
  const leftTokens = new Set(tokenize(left));
  const rightTokens = new Set(tokenize(right));
  if (leftTokens.size === 0 || rightTokens.size === 0) {
    return 0;
  }
  const matches = [...leftTokens].filter((token) => rightTokens.has(token)).length;
  return matches / Math.max(leftTokens.size, rightTokens.size);
}

export function buildInvestigationEvidence(execution: {
  collectedEvidence: CollectedEvidenceItem[];
}, opts: { browserContext?: InvestigationBrowserContext } = {}): Evidence[] {
  const evidence = execution.collectedEvidence.map((item, index) => ({
    id: item.id ?? `e${index}`,
    kind: item.isRedHerring ? "red_herring" : "observation",
    source: "scenario execution",
    summary: item.content,
    supports: [],
    contradicts: [],
    isRedHerring: !!item.isRedHerring,
  }));
  if (opts.browserContext) {
    return [buildInvestigationBrowserEvidence(opts.browserContext), ...evidence];
  }
  return evidence;
}

export function evaluateInvestigationHypotheses(
  hypothesisData: { hypotheses: Array<{ statement: string; confidence: number }> },
  evidence: Evidence[],
  spec: Record<string, unknown>,
): { evidence: Evidence[]; hypotheses: Hypothesis[] } {
  const annotatedEvidence = evidence.map((item) => ({
    ...item,
    supports: [...item.supports],
    contradicts: [...item.contradicts],
  }));
  const correctDiagnosis = normalizeText(
    String(
      spec.correct_diagnosis
      ?? spec.correctDiagnosis
      ?? spec.diagnosis_target
      ?? spec.diagnosisTarget
      ?? "",
    ),
  );

  const hypotheses = hypothesisData.hypotheses.map((hypothesis, index) => {
    const id = `h${index}`;
    const matchesCorrectDiagnosis =
      correctDiagnosis.length > 0 && similarityScore(hypothesis.statement, correctDiagnosis) >= 0.34;
    let supporting = 0;
    let contradicting = 0;

    for (const item of annotatedEvidence) {
      const overlap = similarityScore(hypothesis.statement, item.summary);
      const related = overlap >= 0.34;
      if (item.isRedHerring) {
        if (related) {
          item.contradicts.push(id);
          contradicting += overlap;
        }
      } else if (related || matchesCorrectDiagnosis) {
        item.supports.push(id);
        supporting += Math.max(overlap, matchesCorrectDiagnosis ? 0.5 : 0);
      }
    }

    let status: Hypothesis["status"] = "unresolved";
    if (supporting > contradicting && supporting > 0) {
      status = "supported";
    } else if (contradicting > supporting && contradicting > 0) {
      status = "contradicted";
    }

    return {
      id,
      statement: hypothesis.statement,
      status,
      confidence: hypothesis.confidence,
    };
  });

  return { evidence: annotatedEvidence, hypotheses };
}

export function buildInvestigationConclusion(
  hypotheses: Hypothesis[],
  evidence: Evidence[],
  opts: { hasBrowserContext?: boolean } = {},
): Conclusion {
  const best = hypotheses
    .filter((hypothesis) => hypothesis.status === "supported")
    .sort((left, right) => right.confidence - left.confidence)[0];

  const redHerrings = evidence.filter((item) => item.isRedHerring).length;
  const limitations: string[] = [];
  if (redHerrings > 0) {
    limitations.push(`${redHerrings} potential red herring(s) in evidence pool`);
  }
  if (hypotheses.some((hypothesis) => hypothesis.status === "unresolved")) {
    limitations.push("Some hypotheses remain unresolved");
  }
  limitations.push(
    opts.hasBrowserContext
      ? "Investigation combines generated scenario reasoning with browser snapshot evidence"
      : "Investigation based on generated scenario — not live system data",
  );

  return {
    bestExplanation: best?.statement ?? "No hypothesis received sufficient support",
    confidence: best?.confidence ?? 0,
    limitations,
  };
}

export function identifyInvestigationUnknowns(
  hypotheses: Hypothesis[],
  evidence: Evidence[],
): string[] {
  const unknowns = hypotheses
    .filter((hypothesis) => hypothesis.status === "unresolved")
    .map((hypothesis) => `Hypothesis "${hypothesis.statement}" needs more evidence`);

  if (evidence.length < 3) {
    unknowns.push("Limited evidence collected — more data sources needed");
  }

  return unknowns;
}

export function recommendInvestigationNextSteps(
  hypotheses: Hypothesis[],
  unknowns: string[],
): string[] {
  const steps: string[] = [];
  const supported = hypotheses.filter((hypothesis) => hypothesis.status === "supported");
  if (supported.length > 0) {
    steps.push(`Verify leading hypothesis: "${supported[0].statement}"`);
  }

  for (const hypothesis of hypotheses.filter((item) => item.status === "unresolved").slice(0, 2)) {
    steps.push(`Gather evidence for: "${hypothesis.statement}"`);
  }

  if (unknowns.length > 0) {
    steps.push("Address identified unknowns before concluding");
  }

  return steps;
}
