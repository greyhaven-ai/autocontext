import { CURRENT_SCHEMA_VERSION } from "../contract/schema-version.js";
import type {
  Artifact,
  CostMetric,
  EvalRun,
  LatencyMetric,
  PromotionDecision,
  PromotionThresholds,
  SafetyRegression,
} from "../contract/types.js";
import { computeConfidence } from "./thresholds.js";

export interface DecidePromotionInputs {
  readonly candidate: { artifact: Artifact; evalRun: EvalRun };
  readonly baseline: { artifact: Artifact; evalRun: EvalRun } | null;
  readonly thresholds: PromotionThresholds;
  readonly evaluatedAt: string;
}

/**
 * Pure function: given a candidate and (optional) baseline with their respective
 * EvalRuns and the threshold configuration, produce a PromotionDecision.
 *
 * No I/O, no wall-clock reads. Output is a deterministic function of inputs.
 * Safety regressions are a hard constraint: any regression forces
 * pass=false, recommendedTargetState=disabled regardless of other dims.
 */
export function decidePromotion(inputs: DecidePromotionInputs): PromotionDecision {
  const { candidate, baseline, thresholds, evaluatedAt } = inputs;
  const cm = candidate.evalRun.metrics;
  const bm = baseline?.evalRun.metrics;

  // --- Quality delta ---
  const qualityBaseline = bm?.quality.score ?? 0;
  const qualityCandidate = cm.quality.score;
  const qualityDelta = qualityCandidate - qualityBaseline;
  const qualityPassed = baseline === null ? true : qualityDelta >= thresholds.qualityMinDelta;

  // --- Cost delta (lower is better) ---
  const costBaseline: CostMetric = bm?.cost ?? { tokensIn: 0, tokensOut: 0 };
  const costCandidate: CostMetric = cm.cost;
  const costDelta: CostMetric = {
    tokensIn: costCandidate.tokensIn - costBaseline.tokensIn,
    tokensOut: costCandidate.tokensOut - costBaseline.tokensOut,
    ...(costCandidate.usd !== undefined || costBaseline.usd !== undefined
      ? { usd: (costCandidate.usd ?? 0) - (costBaseline.usd ?? 0) }
      : {}),
  };
  const costPassed = baseline === null ? true : relIncrease(costCandidate.tokensOut, costBaseline.tokensOut) <= thresholds.costMaxRelativeIncrease;

  // --- Latency delta (lower is better) ---
  const latencyBaseline: LatencyMetric = bm?.latency ?? { p50Ms: 0, p95Ms: 0, p99Ms: 0 };
  const latencyCandidate: LatencyMetric = cm.latency;
  const latencyDelta: LatencyMetric = {
    p50Ms: latencyCandidate.p50Ms - latencyBaseline.p50Ms,
    p95Ms: latencyCandidate.p95Ms - latencyBaseline.p95Ms,
    p99Ms: latencyCandidate.p99Ms - latencyBaseline.p99Ms,
  };
  const latencyPassed = baseline === null ? true : relIncrease(latencyCandidate.p95Ms, latencyBaseline.p95Ms) <= thresholds.latencyMaxRelativeIncrease;

  // --- Safety (hard constraint) ---
  const regressions: readonly SafetyRegression[] = cm.safety.regressions;
  const safetyPassed = regressions.length === 0;

  // --- Human feedback (optional) ---
  const humanFeedback = computeHumanFeedbackDelta(cm, bm, thresholds);

  // --- Aggregate pass ---
  const hfOk = humanFeedback?.passed ?? true;
  const pass = safetyPassed && qualityPassed && costPassed && latencyPassed && hfOk;

  // --- Confidence ---
  const minSamples = Math.min(
    cm.quality.sampleSize,
    baseline?.evalRun.metrics.quality.sampleSize ?? Number.POSITIVE_INFINITY,
  );
  const confidence = computeConfidence(minSamples);

  // --- Rollout recommendation ---
  const recommendedTargetState = recommendState({
    pass,
    hasBaseline: baseline !== null,
    qualityDelta,
    confidence,
    costRel: baseline === null ? 0 : relIncrease(costCandidate.tokensOut, costBaseline.tokensOut),
    latencyRel: baseline === null ? 0 : relIncrease(latencyCandidate.p95Ms, latencyBaseline.p95Ms),
    safetyPassed,
    thresholds,
  });

  // --- Reasoning ---
  const reasoning = buildReasoning({ pass, safetyPassed, qualityPassed, costPassed, latencyPassed, confidence, qualityDelta, hasBaseline: baseline !== null });

  const decision: PromotionDecision = {
    schemaVersion: CURRENT_SCHEMA_VERSION,
    pass,
    recommendedTargetState,
    deltas: {
      quality: { baseline: qualityBaseline, candidate: qualityCandidate, delta: qualityDelta, passed: qualityPassed },
      cost:    { baseline: costBaseline,    candidate: costCandidate,    delta: costDelta,    passed: costPassed },
      latency: { baseline: latencyBaseline, candidate: latencyCandidate, delta: latencyDelta, passed: latencyPassed },
      safety:  { regressions, passed: safetyPassed },
      ...(humanFeedback ? { humanFeedback } : {}),
    },
    confidence,
    thresholds,
    reasoning,
    evaluatedAt,
  };
  return decision;
}

function relIncrease(candidate: number, baseline: number): number {
  if (baseline <= 0) return candidate > 0 ? Number.POSITIVE_INFINITY : 0;
  return (candidate - baseline) / baseline;
}

function computeHumanFeedbackDelta(
  candidate: { humanFeedback?: { positive: number; negative: number; neutral: number } },
  baseline: { humanFeedback?: { positive: number; negative: number; neutral: number } } | undefined,
  thresholds: PromotionThresholds,
): { delta: number; passed: boolean } | undefined {
  if (!candidate.humanFeedback) return undefined;
  const candidateScore = candidate.humanFeedback.positive - candidate.humanFeedback.negative;
  const baselineScore = baseline?.humanFeedback
    ? baseline.humanFeedback.positive - baseline.humanFeedback.negative
    : 0;
  const delta = candidateScore - baselineScore;
  const min = thresholds.humanFeedbackMinDelta;
  const passed = min === undefined ? true : delta >= min;
  return { delta, passed };
}

interface RecommendInputs {
  pass: boolean;
  hasBaseline: boolean;
  qualityDelta: number;
  confidence: number;
  costRel: number;
  latencyRel: number;
  safetyPassed: boolean;
  thresholds: PromotionThresholds;
}

function recommendState(i: RecommendInputs): "shadow" | "canary" | "active" | "disabled" {
  if (!i.safetyPassed) return "disabled";
  if (!i.pass) return "disabled";
  // No-baseline case: always shadow — need an incumbent to escalate.
  if (!i.hasBaseline) return "shadow";

  const t = i.thresholds;
  const strongQualityDelta = i.qualityDelta >= t.strongQualityMultiplier * t.qualityMinDelta;
  const strongConfidence   = i.confidence >= t.strongConfidenceMin;
  const costHalfBudget     = i.costRel    <= t.costMaxRelativeIncrease / 2;
  const latencyHalfBudget  = i.latencyRel <= t.latencyMaxRelativeIncrease / 2;

  if (strongQualityDelta && strongConfidence && costHalfBudget && latencyHalfBudget) {
    return "active";
  }

  const moderateConfidence   = i.confidence >= t.moderateConfidenceMin;
  const meetsMinQualityDelta = i.qualityDelta >= t.qualityMinDelta;
  if (moderateConfidence && meetsMinQualityDelta) {
    return "canary";
  }

  return "shadow";
}

function buildReasoning(i: {
  pass: boolean;
  safetyPassed: boolean;
  qualityPassed: boolean;
  costPassed: boolean;
  latencyPassed: boolean;
  confidence: number;
  qualityDelta: number;
  hasBaseline: boolean;
}): string {
  if (!i.safetyPassed) return "Safety regressions present — rejected regardless of other dimensions.";
  if (!i.hasBaseline) return `No incumbent baseline; candidate gets shadow to enable future comparison.`;
  const parts: string[] = [];
  parts.push(`quality Δ=${i.qualityDelta.toFixed(3)} ${i.qualityPassed ? "OK" : "FAIL"}`);
  parts.push(`cost ${i.costPassed ? "OK" : "FAIL"}`);
  parts.push(`latency ${i.latencyPassed ? "OK" : "FAIL"}`);
  parts.push(`confidence=${i.confidence.toFixed(2)}`);
  return `${i.pass ? "Pass" : "Fail"}: ${parts.join(", ")}.`;
}
