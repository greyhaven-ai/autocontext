import type { AgentTaskResult, RoundResult } from "../types/index.js";

export const PLATEAU_EPSILON = 0.01;
export const NEAR_THRESHOLD_MARGIN = 0.02;
export const PLATEAU_PATIENCE = 2;
export const DIMENSION_DELTA_THRESHOLD = 0.05;

export function updateDimensionTrajectory(
  dimensionTrajectory: Record<string, number[]>,
  dimensionScores: Record<string, number>,
): void {
  for (const [dimension, score] of Object.entries(dimensionScores)) {
    if (!(dimension in dimensionTrajectory)) {
      dimensionTrajectory[dimension] = [];
    }
    dimensionTrajectory[dimension].push(score);
  }
}

export function applyScoreDeltaPolicy(opts: {
  score: number;
  prevValidScore: number | null;
  maxScoreDelta: number;
  capScoreJumps: boolean;
  roundNum: number;
}): { effectiveScore: number; warning?: string } {
  if (opts.prevValidScore === null) {
    return { effectiveScore: opts.score };
  }

  const delta = Math.abs(opts.score - opts.prevValidScore);
  if (delta <= opts.maxScoreDelta) {
    return { effectiveScore: opts.score };
  }

  const warning =
    `Score jump of ${delta.toFixed(3)} exceeds maxScoreDelta ${opts.maxScoreDelta} ` +
    `(round ${opts.roundNum}: ${opts.prevValidScore.toFixed(3)} -> ${opts.score.toFixed(3)})`;

  if (!opts.capScoreJumps) {
    return { effectiveScore: opts.score, warning };
  }

  return {
    effectiveScore: Math.max(
      0,
      opts.score > opts.prevValidScore
        ? opts.prevValidScore + opts.maxScoreDelta
        : opts.prevValidScore - opts.maxScoreDelta,
    ),
    warning,
  };
}

export function evaluatePlateauState(opts: {
  prevValidScore: number | null;
  score: number;
  plateauCount: number;
  roundNum: number;
  minRounds: number;
}): { plateauCount: number; shouldStop: boolean } {
  if (
    opts.prevValidScore !== null
    && Math.abs(opts.score - opts.prevValidScore) < PLATEAU_EPSILON
  ) {
    const plateauCount = opts.plateauCount + 1;
    return {
      plateauCount,
      shouldStop: plateauCount >= PLATEAU_PATIENCE && opts.roundNum >= opts.minRounds,
    };
  }

  return { plateauCount: 0, shouldStop: false };
}

export function evaluateThresholdState(opts: {
  effectiveScore: number;
  qualityThreshold: number;
  roundNum: number;
  minRounds: number;
  maxRounds: number;
  thresholdMetRound: number | null;
  dimensionScores: Record<string, number>;
  dimensionThreshold: number | null;
}): {
  metThreshold: boolean;
  shouldStop: boolean;
  thresholdMetRound: number | null;
} {
  let dimensionsSatisfied = true;
  if (opts.dimensionThreshold !== null && Object.keys(opts.dimensionScores).length > 0) {
    dimensionsSatisfied = Object.values(opts.dimensionScores).every(
      (score) => score >= opts.dimensionThreshold!,
    );
  }

  if (
    opts.effectiveScore >= opts.qualityThreshold
    && opts.roundNum >= opts.minRounds
    && dimensionsSatisfied
  ) {
    const nearThreshold = opts.effectiveScore < opts.qualityThreshold + NEAR_THRESHOLD_MARGIN;

    if (opts.thresholdMetRound !== null) {
      return {
        metThreshold: true,
        shouldStop: true,
        thresholdMetRound: opts.thresholdMetRound,
      };
    }

    if (nearThreshold && opts.roundNum < opts.maxRounds) {
      return {
        metThreshold: false,
        shouldStop: false,
        thresholdMetRound: opts.roundNum,
      };
    }

    return {
      metThreshold: true,
      shouldStop: true,
      thresholdMetRound: opts.roundNum,
    };
  }

  return {
    metThreshold: false,
    shouldStop: false,
    thresholdMetRound: null,
  };
}

export function buildRevisionFeedbackResult(opts: {
  result: AgentTaskResult;
  previousValidRound?: RoundResult;
}): AgentTaskResult {
  if (Object.keys(opts.result.dimensionScores).length === 0) {
    return opts.result;
  }

  const previousDimensions = opts.previousValidRound?.dimensionScores ?? {};
  const dimensionLines: string[] = [];

  for (const [dimension, score] of Object.entries(opts.result.dimensionScores).sort()) {
    let line = `  - ${dimension}: ${score.toFixed(2)}`;
    if (dimension in previousDimensions) {
      const delta = score - previousDimensions[dimension];
      if (delta < -DIMENSION_DELTA_THRESHOLD) {
        line += ` (REGRESSION from ${previousDimensions[dimension].toFixed(2)} -- preserve this dimension)`;
      } else if (delta > DIMENSION_DELTA_THRESHOLD) {
        line += ` (improved from ${previousDimensions[dimension].toFixed(2)})`;
      }
    }
    dimensionLines.push(line);
  }

  return {
    score: opts.result.score,
    reasoning: `${opts.result.reasoning}\n\nDimension Scores:\n${dimensionLines.join("\n")}`,
    dimensionScores: opts.result.dimensionScores,
    internalRetries: opts.result.internalRetries,
  };
}
