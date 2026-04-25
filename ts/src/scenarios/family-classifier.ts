import type { ScenarioFamilyName } from "./families.js";
import { SCENARIO_TYPE_MARKERS } from "./families.js";
import {
  buildDefaultFamilyClassification,
  buildRankedFamilyClassification,
  scoreSignals,
} from "./family-classifier-scoring.js";
import { FAMILY_SIGNAL_GROUPS } from "./family-classifier-signals.js";

export type LlmFn = (system: string, user: string) => string;

export interface FamilyCandidate {
  familyName: ScenarioFamilyName;
  confidence: number;
  rationale: string;
}

export interface FamilyClassification {
  familyName: ScenarioFamilyName;
  confidence: number;
  rationale: string;
  alternatives: FamilyCandidate[];
  noSignalsMatched?: boolean;
  llmClassifierUsed?: boolean;
  llmClassifierAttempted?: boolean;
}

export class LowConfidenceError extends Error {
  classification: FamilyClassification;
  minConfidence: number;

  constructor(classification: FamilyClassification, minConfidence: number) {
    const conf = classification.confidence.toFixed(2);
    const thr = minConfidence.toFixed(2);
    let msg: string;
    if (classification.noSignalsMatched) {
      const fallbackNote = classification.llmClassifierAttempted
        ? " LLM fallback was attempted but returned no parseable response."
        : "";
      msg =
        `Family classification confidence ${conf} < threshold ${thr}: ` +
        `no family keywords matched in description (fell back to ${classification.familyName}).` +
        fallbackNote +
        ` Consider rephrasing with domain keywords.`;
    } else {
      msg = `Family classification confidence ${conf} is below threshold ${thr} for family '${classification.familyName}'`;
    }
    super(msg);
    this.classification = classification;
    this.minConfidence = minConfidence;
  }
}

// ---------------------------------------------------------------------------
// LLM classifier (AC-628)
// ---------------------------------------------------------------------------

const _LLM_SYSTEM_PROMPT =
  "You classify a natural-language scenario description into one of the " +
  "registered scenario families. Respond with a single JSON object on one line: " +
  '{"family": "<name>", "confidence": <0.0-1.0>, "rationale": "<short explanation>"}. ' +
  "The family name MUST be one of: {family_list}. Do not invent new family names.";

function _llmClassify(
  description: string,
  families: ScenarioFamilyName[],
  llmFn: LlmFn,
): FamilyClassification | null {
  const system = _LLM_SYSTEM_PROMPT.replace("{family_list}", families.join(", "));
  let raw: string;
  try {
    raw = llmFn(system, description);
  } catch {
    return null;
  }

  const jsonStart = raw.indexOf("{");
  const jsonEnd = raw.lastIndexOf("}");
  if (jsonStart === -1 || jsonEnd === -1 || jsonEnd <= jsonStart) return null;

  let payload: unknown;
  try {
    payload = JSON.parse(raw.slice(jsonStart, jsonEnd + 1));
  } catch {
    return null;
  }

  if (typeof payload !== "object" || payload === null) return null;
  const p = payload as Record<string, unknown>;

  const family = p["family"];
  const confidence = p["confidence"];
  const rationale = p["rationale"];

  if (typeof family !== "string" || !families.includes(family as ScenarioFamilyName)) return null;
  if (typeof rationale !== "string" || !rationale.trim()) return null;

  const confNum = Number(confidence);
  if (isNaN(confNum)) return null;
  const clamped = Math.max(0, Math.min(1, confNum));

  return {
    familyName: family as ScenarioFamilyName,
    confidence: Math.round(clamped * 10000) / 10000,
    rationale,
    alternatives: families
      .filter((f) => f !== family)
      .map((f) => ({
        familyName: f,
        confidence: 0,
        rationale: "LLM classifier selected a different family",
      })),
    noSignalsMatched: false,
    llmClassifierUsed: true,
  };
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export function classifyScenarioFamily(
  description: string,
  options?: { llmFn?: LlmFn },
): FamilyClassification {
  if (!description.trim()) {
    throw new Error("description must be non-empty");
  }

  const families = Object.keys(SCENARIO_TYPE_MARKERS) as ScenarioFamilyName[];
  const textLower = description.toLowerCase();
  const rawScores = new Map<ScenarioFamilyName, number>();
  const matchedSignals = new Map<ScenarioFamilyName, string[]>();

  for (const familyName of families) {
    const [score, matched] = scoreSignals(textLower, FAMILY_SIGNAL_GROUPS[familyName] ?? {});
    rawScores.set(familyName, score);
    matchedSignals.set(familyName, matched);
  }

  const total = [...rawScores.values()].reduce((sum, score) => sum + score, 0);
  const thresholdRaw = process.env["AUTOCONTEXT_CLASSIFIER_FAST_PATH_THRESHOLD"] ?? "0.65";
  const threshold = parseFloat(thresholdRaw);
  const llmFn = options?.llmFn;

  if (total === 0) {
    let llmClassifierAttempted = false;
    if (llmFn) {
      const llmResult = _llmClassify(description, families, llmFn);
      if (llmResult !== null) return llmResult;
      llmClassifierAttempted = true;
    }
    throw new LowConfidenceError(
      {
        ...buildDefaultFamilyClassification(families),
        noSignalsMatched: true,
        llmClassifierAttempted,
      },
      threshold,
    );
  }

  const ranked = buildRankedFamilyClassification({ families, rawScores, matchedSignals, total });

  // Gate 1 — fast-path: high-confidence keywords skip LLM.
  if (ranked.confidence >= threshold) {
    return ranked;
  }

  // Gate 2 — ambiguous: call LLM when available; return keyword result on failure.
  let llmClassifierAttempted = false;
  if (llmFn) {
    const llmResult = _llmClassify(description, families, llmFn);
    if (llmResult !== null) return llmResult;
    llmClassifierAttempted = true;
  }

  return { ...ranked, llmClassifierAttempted };
}

export function routeToFamily(
  classification: FamilyClassification,
  minConfidence = 0.3,
): ScenarioFamilyName {
  if (classification.confidence < minConfidence) {
    throw new LowConfidenceError(classification, minConfidence);
  }
  return classification.familyName;
}
