import {
  createGenerationPhaseState,
  didAdvanceGenerationPhase,
  getFinalizedGenerationPhaseAttempt,
  type GenerationPhaseState,
} from "./generation-phase-state.js";

export interface GenerationCycleState {
  targetGenerations: number;
  completedGenerations: number;
  previousBestOverall: number;
  activeGeneration: GenerationPhaseState | null;
}

export interface CreateGenerationCycleStateOpts {
  targetGenerations: number;
}

export function createGenerationCycleState(
  opts: CreateGenerationCycleStateOpts,
): GenerationCycleState {
  return {
    targetGenerations: opts.targetGenerations,
    completedGenerations: 0,
    previousBestOverall: 0,
    activeGeneration: null,
  };
}

export function hasRemainingGenerationCycles(
  state: GenerationCycleState,
): boolean {
  return state.completedGenerations < state.targetGenerations;
}

export function startNextGenerationCycle(
  state: GenerationCycleState,
): GenerationCycleState {
  if (state.activeGeneration) {
    throw new Error(
      `generation ${state.activeGeneration.generation} is already in progress`,
    );
  }
  if (!hasRemainingGenerationCycles(state)) {
    throw new Error("no generation cycles remaining");
  }

  return {
    ...state,
    activeGeneration: createGenerationPhaseState({
      generation: state.completedGenerations + 1,
      previousBestForGeneration: state.previousBestOverall,
    }),
  };
}

export function updateGenerationCyclePhase(
  state: GenerationCycleState,
  phaseState: GenerationPhaseState,
): GenerationCycleState {
  return {
    ...state,
    activeGeneration: phaseState,
  };
}

export function getActiveGenerationPhaseState(
  state: GenerationCycleState,
): GenerationPhaseState {
  if (!state.activeGeneration) {
    throw new Error("no active generation in progress");
  }

  return state.activeGeneration;
}

export function completeGenerationCycle(
  state: GenerationCycleState,
): GenerationCycleState {
  const activeGeneration = getActiveGenerationPhaseState(state);
  const finalizedAttempt = getFinalizedGenerationPhaseAttempt(activeGeneration);

  return {
    ...state,
    completedGenerations: activeGeneration.generation,
    previousBestOverall: didAdvanceGenerationPhase(activeGeneration)
      ? Math.max(
          state.previousBestOverall,
          finalizedAttempt.tournamentResult.bestScore,
        )
      : state.previousBestOverall,
    activeGeneration: null,
  };
}
