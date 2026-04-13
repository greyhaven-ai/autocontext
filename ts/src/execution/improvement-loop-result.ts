import type {
  AgentTaskResult,
  ImprovementResult,
  RoundResult,
} from "../types/index.js";

function findWorstDimension(dimensionScores: Record<string, number>): {
  worstDimension: string | undefined;
  worstDimensionScore: number | undefined;
} {
  const entries = Object.entries(dimensionScores);
  if (entries.length === 0) {
    return { worstDimension: undefined, worstDimensionScore: undefined };
  }

  let [worstDimension, worstDimensionScore] = entries[0];
  for (let index = 1; index < entries.length; index += 1) {
    const [dimension, score] = entries[index];
    if (score < worstDimensionScore) {
      worstDimension = dimension;
      worstDimensionScore = score;
    }
  }

  return { worstDimension, worstDimensionScore };
}

export function buildRoundResult(opts: {
  roundNumber: number;
  output: string;
  result: AgentTaskResult;
  judgeFailed: boolean;
  roundDurationMs: number;
}): RoundResult {
  const worstDimension = opts.judgeFailed
    ? { worstDimension: undefined, worstDimensionScore: undefined }
    : findWorstDimension(opts.result.dimensionScores);

  return {
    roundNumber: opts.roundNumber,
    output: opts.output,
    score: opts.result.score,
    reasoning: opts.result.reasoning,
    dimensionScores: opts.result.dimensionScores,
    isRevision: opts.roundNumber > 1,
    judgeFailed: opts.judgeFailed,
    worstDimension: worstDimension.worstDimension,
    worstDimensionScore: worstDimension.worstDimensionScore,
    roundDurationMs: opts.roundDurationMs,
  };
}

export function buildImprovementResult(opts: {
  rounds: RoundResult[];
  bestOutput: string;
  bestScore: number;
  bestRound: number;
  totalRounds?: number;
  metThreshold: boolean;
  judgeFailures: number;
  terminationReason: ImprovementResult["terminationReason"];
  dimensionTrajectory: Record<string, number[]>;
  totalInternalRetries: number;
  durationMs: number;
  judgeCalls: number;
}): ImprovementResult {
  return {
    rounds: opts.rounds,
    bestOutput: opts.bestOutput,
    bestScore: opts.bestScore,
    bestRound: opts.bestRound,
    totalRounds: opts.totalRounds ?? opts.rounds.length,
    metThreshold: opts.metThreshold,
    judgeFailures: opts.judgeFailures,
    terminationReason: opts.terminationReason,
    dimensionTrajectory: opts.dimensionTrajectory,
    totalInternalRetries: opts.totalInternalRetries,
    durationMs: opts.durationMs,
    judgeCalls: opts.judgeCalls,
  };
}
