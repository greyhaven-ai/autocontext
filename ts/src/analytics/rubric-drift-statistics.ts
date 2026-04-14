import { randomUUID } from "node:crypto";
import { roundToDecimals } from "./number-utils.js";
import type { RubricSnapshot, RunFacetLike } from "./rubric-drift-types.js";

export const PERFECT_THRESHOLD = 0.95;

export function mean(values: number[]): number {
  if (values.length === 0) {
    return 0;
  }
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

export function median(values: number[]): number {
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

export function populationStddev(values: number[]): number {
  if (values.length <= 1) {
    return 0;
  }
  const average = mean(values);
  const variance = values.reduce((sum, value) => sum + (value - average) ** 2, 0) / values.length;
  return Math.sqrt(variance);
}

export function syntheticTimestamp(index: number): string {
  return new Date(Date.UTC(2026, 0, 1, 0, 0, index)).toISOString();
}

export function randomId(prefix: string): string {
  return `${prefix}-${randomUUID().slice(0, 8)}`;
}

export function computeRubricSnapshot(
  facets: readonly RunFacetLike[],
  opts: { release?: string; scenarioFamily?: string; agentProvider?: string } = {},
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
      release: opts.release ?? "",
      scenarioFamily: opts.scenarioFamily ?? "",
      agentProvider: opts.agentProvider ?? "",
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
    meanScore: roundToDecimals(mean(scores), 4),
    medianScore: roundToDecimals(median(scores), 4),
    stddevScore: roundToDecimals(populationStddev(scores), 4),
    minScore: Math.min(...scores),
    maxScore: Math.max(...scores),
    scoreInflationRate: roundToDecimals(scoreInflationRate, 4),
    perfectScoreRate: roundToDecimals(perfectCount / facets.length, 4),
    revisionJumpRate: roundToDecimals(totalGenerations > 0 ? strongImprovements / totalGenerations : 0, 4),
    retryRate: roundToDecimals(totalGenerations > 0 ? retryCount / totalGenerations : 0, 4),
    rollbackRate: roundToDecimals(totalGenerations > 0 ? rollbackCount / totalGenerations : 0, 4),
    release: opts.release ?? "",
    scenarioFamily: opts.scenarioFamily ?? "",
    agentProvider: opts.agentProvider ?? "",
    metadata: { scenarios },
  };
}
