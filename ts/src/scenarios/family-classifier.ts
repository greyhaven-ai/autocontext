import type { ScenarioFamilyName } from "./families.js";
import { SCENARIO_TYPE_MARKERS } from "./families.js";
import { buildDefaultFamilyClassification, buildRankedFamilyClassification, scoreSignals } from "./family-classifier-scoring.js";
import { FAMILY_SIGNAL_GROUPS } from "./family-classifier-signals.js";

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
}

export class LowConfidenceError extends Error {
  classification: FamilyClassification;
  minConfidence: number;

  constructor(classification: FamilyClassification, minConfidence: number) {
    super(
      `Family classification confidence ${classification.confidence.toFixed(2)} is below threshold ${minConfidence.toFixed(2)} for family '${classification.familyName}'`,
    );
    this.classification = classification;
    this.minConfidence = minConfidence;
  }
}

export function classifyScenarioFamily(description: string): FamilyClassification {
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
  if (total === 0) {
    return buildDefaultFamilyClassification(families);
  }

  return buildRankedFamilyClassification({
    families,
    rawScores,
    matchedSignals,
    total,
  });
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
