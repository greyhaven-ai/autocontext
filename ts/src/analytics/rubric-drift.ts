/**
 * Rubric-drift monitoring for score inflation and stability detection.
 *
 * TS port of autocontext.analytics.rubric_drift (AC-381).
 */

function round(value: number, digits = 4): number {
  return Number(value.toFixed(digits));
}

function mean(values: number[]): number {
  if (values.length === 0) {
    return 0;
  }
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function median(values: number[]): number {
  if (values.length === 0) {
    return 0;
  }
  const sorted = [...values].sort((left, right) => left - right);
  const middle = Math.floor(sorted.length / 2);
  if (sorted.length % 2 === 1) {
    return sorted[middle];
  }
  return (sorted[middle - 1] + sorted[middle]) / 2;
}

function populationStddev(values: number[]): number {
  if (values.length <= 1) {
    return 0;
  }
  const average = mean(values);
  const variance = values.reduce((sum, value) => sum + (value - average) ** 2, 0) / values.length;
  return Math.sqrt(variance);
}

function syntheticTimestamp(index: number): string {
  return new Date(Date.UTC(2026, 0, 1, 0, 0, index)).toISOString();
}

function randomId(prefix: string): string {
  return `${prefix}-${globalThis.crypto.randomUUID().slice(0, 8)}`;
}

export interface DelightSignalLike {
  signalType: string;
}

export interface RunFacetLike {
  scenario: string;
  bestScore: number;
  createdAt?: string;
  totalGenerations?: number;
  delightSignals?: DelightSignalLike[];
  retries?: number;
  rollbacks?: number;
}

export interface RubricSnapshot {
  snapshotId: string;
  createdAt: string;
  windowStart: string;
  windowEnd: string;
  runCount: number;
  meanScore: number;
  medianScore: number;
  stddevScore: number;
  minScore: number;
  maxScore: number;
  scoreInflationRate: number;
  perfectScoreRate: number;
  revisionJumpRate: number;
  retryRate: number;
  rollbackRate: number;
  release: string;
  scenarioFamily: string;
  agentProvider: string;
  metadata: Record<string, unknown>;
}

export interface DriftThresholds {
  maxScoreInflation: number;
  maxPerfectRate: number;
  maxRevisionJumpRate: number;
  minStddev: number;
  maxRetryRate: number;
  maxRollbackRate: number;
}

export interface DriftWarning {
  warningId: string;
  createdAt: string;
  warningType: string;
  severity: string;
  description: string;
  snapshotId: string;
  metricName: string;
  metricValue: number;
  thresholdValue: number;
  affectedScenarios: string[];
  affectedProviders: string[];
  affectedReleases: string[];
  metadata: Record<string, unknown>;
}

export interface DriftReport {
  snapshot: RubricSnapshot;
  warnings: DriftWarning[];
  stable: boolean;
  meanScore: number;
  scoreCount: number;
}

const PERFECT_THRESHOLD = 0.95;

const DEFAULT_THRESHOLDS: DriftThresholds = {
  maxScoreInflation: 0.15,
  maxPerfectRate: 0.5,
  maxRevisionJumpRate: 0.4,
  minStddev: 0.05,
  maxRetryRate: 0.5,
  maxRollbackRate: 0.3,
};

function makeWarning(
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
    metricValue: round(metricValue, 4),
    thresholdValue: round(thresholdValue, 4),
    affectedScenarios,
    affectedProviders,
    affectedReleases,
    metadata: {},
  };
}

export class RubricDriftMonitor {
  private readonly thresholds: DriftThresholds;
  private readonly recordedFacets: RunFacetLike[] = [];

  constructor(thresholds: Partial<DriftThresholds> = {}) {
    this.thresholds = { ...DEFAULT_THRESHOLDS, ...thresholds };
  }

  recordScore(score: number): void {
    this.recordedFacets.push({
      scenario: "",
      bestScore: score,
      createdAt: syntheticTimestamp(this.recordedFacets.length),
      totalGenerations: 1,
      delightSignals: [],
      retries: 0,
      rollbacks: 0,
    });
  }

  computeSnapshot(
    facets: readonly RunFacetLike[],
    release = "",
    scenarioFamily = "",
    agentProvider = "",
  ): RubricSnapshot {
    const now = new Date().toISOString();
    const scenarios = [...new Set(facets.map((facet) => facet.scenario).filter(Boolean))].sort();

    if (facets.length === 0) {
      return {
        snapshotId: randomId("snap"),
        createdAt: now,
        windowStart: "",
        windowEnd: "",
        runCount: 0,
        meanScore: 0,
        medianScore: 0,
        stddevScore: 0,
        minScore: 0,
        maxScore: 0,
        scoreInflationRate: 0,
        perfectScoreRate: 0,
        revisionJumpRate: 0,
        retryRate: 0,
        rollbackRate: 0,
        release,
        scenarioFamily,
        agentProvider,
        metadata: { scenarios },
      };
    }

    const scores = facets.map((facet) => facet.bestScore);
    const timestamps = facets
      .map((facet) => facet.createdAt ?? "")
      .filter((timestamp) => timestamp.length > 0)
      .sort();

    const perfectCount = scores.filter((score) => score >= PERFECT_THRESHOLD).length;
    const sortedFacets = [...facets].sort((left, right) => (left.createdAt ?? "").localeCompare(right.createdAt ?? ""));
    const midpoint = Math.floor(sortedFacets.length / 2);

    let scoreInflationRate = 0;
    if (midpoint > 0) {
      const firstHalfMean = mean(sortedFacets.slice(0, midpoint).map((facet) => facet.bestScore));
      const secondHalfMean = mean(sortedFacets.slice(midpoint).map((facet) => facet.bestScore));
      scoreInflationRate = secondHalfMean - firstHalfMean;
    }

    const totalGenerations = facets.reduce((sum, facet) => sum + (facet.totalGenerations ?? 0), 0);
    const strongImprovements = facets.reduce((sum, facet) => {
      const signals = facet.delightSignals ?? [];
      return sum + signals.filter((signal) => signal.signalType === "strong_improvement").length;
    }, 0);
    const retryCount = facets.reduce((sum, facet) => sum + (facet.retries ?? 0), 0);
    const rollbackCount = facets.reduce((sum, facet) => sum + (facet.rollbacks ?? 0), 0);

    return {
      snapshotId: randomId("snap"),
      createdAt: now,
      windowStart: timestamps[0] ?? "",
      windowEnd: timestamps[timestamps.length - 1] ?? "",
      runCount: facets.length,
      meanScore: round(mean(scores), 4),
      medianScore: round(median(scores), 4),
      stddevScore: round(populationStddev(scores), 4),
      minScore: Math.min(...scores),
      maxScore: Math.max(...scores),
      scoreInflationRate: round(scoreInflationRate, 4),
      perfectScoreRate: round(perfectCount / facets.length, 4),
      revisionJumpRate: round(totalGenerations > 0 ? strongImprovements / totalGenerations : 0, 4),
      retryRate: round(totalGenerations > 0 ? retryCount / totalGenerations : 0, 4),
      rollbackRate: round(totalGenerations > 0 ? rollbackCount / totalGenerations : 0, 4),
      release,
      scenarioFamily,
      agentProvider,
      metadata: { scenarios },
    };
  }

  detectDrift(current: RubricSnapshot, baseline?: RubricSnapshot): DriftWarning[] {
    if (current.runCount === 0) {
      return [];
    }

    const warnings: DriftWarning[] = [];
    const now = new Date().toISOString();

    if (current.scoreInflationRate > this.thresholds.maxScoreInflation) {
      warnings.push(makeWarning(
        now,
        "score_inflation",
        "high",
        `Score inflation rate ${current.scoreInflationRate.toFixed(2)} exceeds threshold ${this.thresholds.maxScoreInflation.toFixed(2)}`,
        current,
        "score_inflation_rate",
        current.scoreInflationRate,
        this.thresholds.maxScoreInflation,
      ));
    }

    if (baseline) {
      const delta = current.meanScore - baseline.meanScore;
      if (delta > this.thresholds.maxScoreInflation) {
        warnings.push(makeWarning(
          now,
          "score_inflation",
          "high",
          `Mean score increased by ${delta.toFixed(2)} from baseline (${baseline.meanScore.toFixed(2)} → ${current.meanScore.toFixed(2)})`,
          current,
          "mean_score_delta",
          delta,
          this.thresholds.maxScoreInflation,
        ));
      }
    }

    if (current.perfectScoreRate > this.thresholds.maxPerfectRate) {
      warnings.push(makeWarning(
        now,
        "perfect_rate_high",
        "high",
        `Perfect score rate ${(current.perfectScoreRate * 100).toFixed(0)}% exceeds threshold ${(this.thresholds.maxPerfectRate * 100).toFixed(0)}%`,
        current,
        "perfect_score_rate",
        current.perfectScoreRate,
        this.thresholds.maxPerfectRate,
      ));
    }

    if (current.stddevScore < this.thresholds.minStddev && current.runCount > 1) {
      warnings.push(makeWarning(
        now,
        "score_compression",
        "medium",
        `Score stddev ${current.stddevScore.toFixed(4)} below minimum ${this.thresholds.minStddev.toFixed(4)}`,
        current,
        "stddev_score",
        current.stddevScore,
        this.thresholds.minStddev,
      ));
    }

    if (current.revisionJumpRate > this.thresholds.maxRevisionJumpRate) {
      warnings.push(makeWarning(
        now,
        "revision_jump_rate_high",
        "medium",
        `Revision jump rate ${(current.revisionJumpRate * 100).toFixed(0)}% exceeds threshold ${(this.thresholds.maxRevisionJumpRate * 100).toFixed(0)}%`,
        current,
        "revision_jump_rate",
        current.revisionJumpRate,
        this.thresholds.maxRevisionJumpRate,
      ));
    }

    if (current.retryRate > this.thresholds.maxRetryRate) {
      warnings.push(makeWarning(
        now,
        "retry_rate_high",
        "medium",
        `Retry rate ${(current.retryRate * 100).toFixed(0)}% exceeds threshold ${(this.thresholds.maxRetryRate * 100).toFixed(0)}%`,
        current,
        "retry_rate",
        current.retryRate,
        this.thresholds.maxRetryRate,
      ));
    }

    if (current.rollbackRate > this.thresholds.maxRollbackRate) {
      warnings.push(makeWarning(
        now,
        "rollback_rate_high",
        "high",
        `Rollback rate ${(current.rollbackRate * 100).toFixed(0)}% exceeds threshold ${(this.thresholds.maxRollbackRate * 100).toFixed(0)}%`,
        current,
        "rollback_rate",
        current.rollbackRate,
        this.thresholds.maxRollbackRate,
      ));
    }

    return warnings;
  }

  analyze(
    facets: readonly RunFacetLike[] = this.recordedFacets,
    options: {
      release?: string;
      scenarioFamily?: string;
      agentProvider?: string;
      baseline?: RubricSnapshot;
    } = {},
  ): DriftReport {
    const snapshot = this.computeSnapshot(
      facets,
      options.release ?? "",
      options.scenarioFamily ?? "",
      options.agentProvider ?? "",
    );
    const warnings = this.detectDrift(snapshot, options.baseline);
    return {
      snapshot,
      warnings,
      stable: warnings.length === 0,
      meanScore: snapshot.meanScore,
      scoreCount: snapshot.runCount,
    };
  }
}
