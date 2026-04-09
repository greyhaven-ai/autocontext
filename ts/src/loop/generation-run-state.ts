export interface GenerationRunState {
  runId: string;
  scenarioName: string;
  targetGenerations: number;
  status: "running" | "completed" | "failed";
  generationsCompleted: number;
  bestScore: number;
  currentElo: number;
  pendingFreshStartHint: string | null;
  startedAtMs: number;
  finishedAtMs: number | null;
  error: string | null;
}

export interface CreateGenerationRunStateOpts {
  runId: string;
  scenarioName: string;
  targetGenerations: number;
  startedAtMs: number;
}

export interface GenerationResultUpdate {
  generation: number;
  bestScore: number;
  elo: number;
}

export interface GenerationRunCompletion {
  finishedAtMs: number;
}

export interface GenerationRunFailure extends GenerationRunCompletion {
  error: string;
}

export function createGenerationRunState(
  opts: CreateGenerationRunStateOpts,
): GenerationRunState {
  return {
    runId: opts.runId,
    scenarioName: opts.scenarioName,
    targetGenerations: opts.targetGenerations,
    status: "running",
    generationsCompleted: 0,
    bestScore: 0,
    currentElo: 1000,
    pendingFreshStartHint: null,
    startedAtMs: opts.startedAtMs,
    finishedAtMs: null,
    error: null,
  };
}

export function recordGenerationResult(
  state: GenerationRunState,
  update: GenerationResultUpdate,
): GenerationRunState {
  return {
    ...state,
    generationsCompleted: Math.max(state.generationsCompleted, update.generation),
    bestScore: Math.max(state.bestScore, update.bestScore),
    currentElo: update.elo,
  };
}

export function queueFreshStartHint(
  state: GenerationRunState,
  hint: string,
): GenerationRunState {
  return {
    ...state,
    pendingFreshStartHint: hint,
  };
}

export function consumeFreshStartHint(
  state: GenerationRunState,
): { hint: string | null; state: GenerationRunState } {
  return {
    hint: state.pendingFreshStartHint,
    state: {
      ...state,
      pendingFreshStartHint: null,
    },
  };
}

export function completeGenerationRun(
  state: GenerationRunState,
  completion: GenerationRunCompletion,
): GenerationRunState {
  return {
    ...state,
    status: "completed",
    finishedAtMs: completion.finishedAtMs,
  };
}

export function failGenerationRun(
  state: GenerationRunState,
  failure: GenerationRunFailure,
): GenerationRunState {
  return {
    ...state,
    status: "failed",
    finishedAtMs: failure.finishedAtMs,
    error: failure.error,
  };
}
