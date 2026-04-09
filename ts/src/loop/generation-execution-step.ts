import type { GenerationAttempt } from "./generation-attempt-state.js";

export const DEFAULT_COMPETITOR_STRATEGY = {
  aggression: 0.5,
  defense: 0.5,
  path_bias: 0.5,
} as const;

export function parseCompetitorStrategyResult(
  competitorResultText: string,
): Record<string, unknown> {
  try {
    return JSON.parse(competitorResultText) as Record<string, unknown>;
  } catch {
    return { ...DEFAULT_COMPETITOR_STRATEGY };
  }
}

export interface TournamentExecutionPlan {
  seedForGeneration: number;
  tournamentOptions: {
    matchCount: number;
    seedBase: number;
    initialElo: number;
  };
}

export function createTournamentExecutionPlan(opts: {
  generation: number;
  seedBase: number;
  matchesPerGeneration: number;
  currentElo: number;
}): TournamentExecutionPlan {
  const seedForGeneration = opts.seedBase + (opts.generation - 1) * opts.matchesPerGeneration;

  return {
    seedForGeneration,
    tournamentOptions: {
      matchCount: opts.matchesPerGeneration,
      seedBase: seedForGeneration,
      initialElo: opts.currentElo,
    },
  };
}

export function buildGenerationAttemptCandidate(
  attempt: GenerationAttempt,
): GenerationAttempt {
  return attempt;
}
