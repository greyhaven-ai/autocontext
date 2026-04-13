import { normalizeConfidence } from "../analytics/number-utils.js";
import type { ScenarioFamilyName } from "./families.js";
import type {
  FamilyCandidate,
  FamilyClassification,
} from "./family-classifier.js";
import { DEFAULT_FAMILY_NAME } from "./family-classifier-signals.js";

export function buildRationale(matched: string[], familyName: ScenarioFamilyName): string {
  if (matched.length === 0) {
    return `No strong signals for ${familyName}`;
  }
  return `Matched ${familyName} signals: ${matched.slice(0, 3).join(", ")}`;
}

export function scoreSignals(
  textLower: string,
  signals: Record<string, number>,
): [number, string[]] {
  let score = 0;
  const matched: string[] = [];

  for (const [signal, weight] of Object.entries(signals)) {
    if (textLower.includes(signal)) {
      score += weight;
      matched.push(signal);
    }
  }

  return [score, matched];
}

export function buildDefaultFamilyClassification(
  families: ScenarioFamilyName[],
): FamilyClassification {
  const defaultFamily = families.includes(DEFAULT_FAMILY_NAME)
    ? DEFAULT_FAMILY_NAME
    : families[0];

  return {
    familyName: defaultFamily,
    confidence: 0.2,
    rationale: `No strong signals detected; defaulting to ${defaultFamily}`,
    alternatives: families
      .filter((familyName) => familyName !== defaultFamily)
      .map((familyName): FamilyCandidate => ({
        familyName,
        confidence: 0.1,
        rationale: `No ${familyName} signals`,
      })),
  };
}

export function buildRankedFamilyClassification(opts: {
  families: ScenarioFamilyName[];
  rawScores: Map<ScenarioFamilyName, number>;
  matchedSignals: Map<ScenarioFamilyName, string[]>;
  total: number;
}): FamilyClassification {
  const ranked = opts.families
    .map((familyName) => ({
      familyName,
      confidence: opts.rawScores.get(familyName)! / opts.total,
    }))
    .sort((a, b) => b.confidence - a.confidence);

  const [top, ...rest] = ranked;
  return {
    familyName: top.familyName,
    confidence: normalizeConfidence(top.confidence),
    rationale: buildRationale(opts.matchedSignals.get(top.familyName) ?? [], top.familyName),
    alternatives: rest.map(({ familyName, confidence }): FamilyCandidate => ({
      familyName,
      confidence: normalizeConfidence(confidence),
      rationale: buildRationale(opts.matchedSignals.get(familyName) ?? [], familyName),
    })),
  };
}
