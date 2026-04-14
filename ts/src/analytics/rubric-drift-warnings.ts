import { roundToDecimals } from "./number-utils.js";
import { randomId } from "./rubric-drift-statistics.js";
import type {
  DriftThresholds,
  DriftWarning,
  RubricSnapshot,
} from "./rubric-drift-types.js";

export const DEFAULT_THRESHOLDS: DriftThresholds = {
  maxScoreInflation: 0.15,
  maxPerfectRate: 0.5,
  maxRevisionJumpRate: 0.4,
  minStddev: 0.05,
  maxRetryRate: 0.5,
  maxRollbackRate: 0.3,
};

export function makeWarning(
  createdAt: string,
  warningType: string,
  severity: string,
  description: string,
  snapshot: RubricSnapshot,
  metricName: string,
  metricValue: number,
  thresholdValue: number,
): DriftWarning {
  const rawScenarios = Array.isArray(snapshot.metadata.scenarios) ? snapshot.metadata.scenarios : [];
  const affectedScenarios = rawScenarios.map((scenario) => String(scenario)).filter(Boolean).sort();
  const affectedProviders = snapshot.agentProvider ? [snapshot.agentProvider] : [];
  const affectedReleases = snapshot.release ? [snapshot.release] : [];

  return {
    warningId: randomId("warn"),
    createdAt,
    warningType,
    severity,
    description,
    snapshotId: snapshot.snapshotId,
    metricName,
    metricValue: roundToDecimals(metricValue, 4),
    thresholdValue: roundToDecimals(thresholdValue, 4),
    affectedScenarios,
    affectedProviders,
    affectedReleases,
    metadata: {},
  };
}

export function detectRubricDrift(
  current: RubricSnapshot,
  thresholds: DriftThresholds,
  baseline?: RubricSnapshot,
): DriftWarning[] {
  if (current.runCount === 0) {
    return [];
  }

  const warnings: DriftWarning[] = [];
  const now = new Date().toISOString();

  if (current.scoreInflationRate > thresholds.maxScoreInflation) {
    warnings.push(makeWarning(
      now,
      "score_inflation",
      "high",
      `Score inflation rate ${current.scoreInflationRate.toFixed(2)} exceeds threshold ${thresholds.maxScoreInflation.toFixed(2)}`,
      current,
      "score_inflation_rate",
      current.scoreInflationRate,
      thresholds.maxScoreInflation,
    ));
  }

  if (baseline) {
    const delta = current.meanScore - baseline.meanScore;
    if (delta > thresholds.maxScoreInflation) {
      warnings.push(makeWarning(
        now,
        "score_inflation",
        "high",
        `Mean score increased by ${delta.toFixed(2)} from baseline (${baseline.meanScore.toFixed(2)} → ${current.meanScore.toFixed(2)})`,
        current,
        "mean_score_delta",
        delta,
        thresholds.maxScoreInflation,
      ));
    }
  }

  if (current.perfectScoreRate > thresholds.maxPerfectRate) {
    warnings.push(makeWarning(
      now,
      "perfect_rate_high",
      "high",
      `Perfect score rate ${(current.perfectScoreRate * 100).toFixed(0)}% exceeds threshold ${(thresholds.maxPerfectRate * 100).toFixed(0)}%`,
      current,
      "perfect_score_rate",
      current.perfectScoreRate,
      thresholds.maxPerfectRate,
    ));
  }

  if (current.stddevScore < thresholds.minStddev && current.runCount > 1) {
    warnings.push(makeWarning(
      now,
      "score_compression",
      "medium",
      `Score stddev ${current.stddevScore.toFixed(4)} below minimum ${thresholds.minStddev.toFixed(4)}`,
      current,
      "stddev_score",
      current.stddevScore,
      thresholds.minStddev,
    ));
  }

  if (current.revisionJumpRate > thresholds.maxRevisionJumpRate) {
    warnings.push(makeWarning(
      now,
      "revision_jump_rate_high",
      "medium",
      `Revision jump rate ${(current.revisionJumpRate * 100).toFixed(0)}% exceeds threshold ${(thresholds.maxRevisionJumpRate * 100).toFixed(0)}%`,
      current,
      "revision_jump_rate",
      current.revisionJumpRate,
      thresholds.maxRevisionJumpRate,
    ));
  }

  if (current.retryRate > thresholds.maxRetryRate) {
    warnings.push(makeWarning(
      now,
      "retry_rate_high",
      "medium",
      `Retry rate ${(current.retryRate * 100).toFixed(0)}% exceeds threshold ${(thresholds.maxRetryRate * 100).toFixed(0)}%`,
      current,
      "retry_rate",
      current.retryRate,
      thresholds.maxRetryRate,
    ));
  }

  if (current.rollbackRate > thresholds.maxRollbackRate) {
    warnings.push(makeWarning(
      now,
      "rollback_rate_high",
      "high",
      `Rollback rate ${(current.rollbackRate * 100).toFixed(0)}% exceeds threshold ${(thresholds.maxRollbackRate * 100).toFixed(0)}%`,
      current,
      "rollback_rate",
      current.rollbackRate,
      thresholds.maxRollbackRate,
    ));
  }

  return warnings;
}
