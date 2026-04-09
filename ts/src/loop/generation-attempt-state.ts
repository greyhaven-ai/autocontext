import type { TournamentResult } from "../execution/tournament.js";

export type GenerationGateDecision = "advance" | "retry" | "rollback";

export interface GenerationAttempt {
  competitorPrompt: string;
  competitorResultText: string;
  strategy: Record<string, unknown>;
  tournamentResult: TournamentResult;
  gateDecision: GenerationGateDecision;
}

export interface GenerationAttemptState {
  generation: number;
  previousBestForGeneration: number;
  retryCount: number;
  finalizedAttempt: GenerationAttempt | null;
  lastAttempt: GenerationAttempt | null;
  status: "in_progress" | "retrying" | "advanced" | "rolled_back";
}

export interface CreateGenerationAttemptStateOpts {
  generation: number;
  previousBestForGeneration: number;
}

export function createGenerationAttemptState(
  opts: CreateGenerationAttemptStateOpts,
): GenerationAttemptState {
  return {
    generation: opts.generation,
    previousBestForGeneration: opts.previousBestForGeneration,
    retryCount: 0,
    finalizedAttempt: null,
    lastAttempt: null,
    status: "in_progress",
  };
}

export function canContinueGenerationAttempt(
  state: GenerationAttemptState,
  maxRetries: number,
): boolean {
  return state.finalizedAttempt === null && state.retryCount <= maxRetries;
}

export function applyGenerationAttemptDecision(
  state: GenerationAttemptState,
  attempt: GenerationAttempt,
): GenerationAttemptState {
  if (attempt.gateDecision === "retry") {
    return {
      ...state,
      retryCount: state.retryCount + 1,
      lastAttempt: attempt,
      status: "retrying",
    };
  }

  if (attempt.gateDecision === "advance") {
    return {
      ...state,
      lastAttempt: attempt,
      finalizedAttempt: attempt,
      status: "advanced",
    };
  }

  return {
    ...state,
    lastAttempt: attempt,
    finalizedAttempt: attempt,
    status: "rolled_back",
  };
}

export function didAdvanceGenerationAttempt(
  state: GenerationAttemptState,
): boolean {
  return state.status === "advanced";
}

export function getFinalizedGenerationAttempt(
  state: GenerationAttemptState,
): GenerationAttempt {
  if (!state.finalizedAttempt) {
    throw new Error(
      `generation ${state.generation} finished without a finalized attempt`,
    );
  }

  return state.finalizedAttempt;
}
