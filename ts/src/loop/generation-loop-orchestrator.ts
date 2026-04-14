import {
  buildAgentsStartedPayload,
  buildGenerationCompletedPayload,
  buildGenerationStartedPayload,
  buildRunCompletedPayload,
  buildRunFailedPayload,
  buildRunStartedPayload,
  type AgentsStartedPayload,
  type GenerationCompletedPayload,
  type GenerationStartedPayload,
  type RunCompletedPayload,
  type RunFailedPayload,
  type RunStartedPayload,
} from "./generation-event-coordinator.js";
import {
  completeGenerationCycle,
  createGenerationCycleState,
  getActiveGenerationPhaseState,
  startNextGenerationCycle,
  updateGenerationCyclePhase,
  type GenerationCycleState,
} from "./generation-cycle-state.js";
import {
  completeGenerationRun,
  createGenerationRunState,
  failGenerationRun,
  recordGenerationResult,
  type GenerationRunState,
} from "./generation-run-state.js";
import type { GenerationGateDecision } from "./generation-attempt-state.js";
import type { GenerationPhaseState } from "./generation-phase-state.js";

export interface GenerationLoopOrchestration {
  runState: GenerationRunState;
  cycleState: GenerationCycleState;
  events: {
    runStarted?: RunStartedPayload;
    generationStarted?: GenerationStartedPayload;
    agentsStarted?: AgentsStartedPayload;
    generationCompleted?: GenerationCompletedPayload;
    runCompleted?: RunCompletedPayload;
    runFailed?: RunFailedPayload;
  };
}

export function createGenerationLoopOrchestration(opts: {
  runId: string;
  scenarioName: string;
  targetGenerations: number;
  startedAtMs: number;
}): GenerationLoopOrchestration {
  return {
    runState: createGenerationRunState({
      runId: opts.runId,
      scenarioName: opts.scenarioName,
      targetGenerations: opts.targetGenerations,
      startedAtMs: opts.startedAtMs,
    }),
    cycleState: createGenerationCycleState({
      targetGenerations: opts.targetGenerations,
    }),
    events: {
      runStarted: buildRunStartedPayload({
        runId: opts.runId,
        scenarioName: opts.scenarioName,
        targetGenerations: opts.targetGenerations,
      }),
    },
  };
}

export function startNextGeneration(
  orchestration: GenerationLoopOrchestration,
  curatorEnabled: boolean,
): GenerationLoopOrchestration {
  const cycleState = startNextGenerationCycle(orchestration.cycleState);
  const generation = getActiveGenerationPhaseState(cycleState).generation;

  return {
    ...orchestration,
    cycleState,
    events: {
      generationStarted: buildGenerationStartedPayload(
        orchestration.runState.runId,
        generation,
      ),
      agentsStarted: buildAgentsStartedPayload(
        orchestration.runState.runId,
        generation,
        curatorEnabled,
      ),
    },
  };
}

export function getActiveGenerationPhase(
  orchestration: GenerationLoopOrchestration,
): GenerationPhaseState {
  return getActiveGenerationPhaseState(orchestration.cycleState);
}

export function recordAdvancedGenerationResult(
  orchestration: GenerationLoopOrchestration,
  update: { generation: number; bestScore: number; elo: number },
): GenerationLoopOrchestration {
  return {
    ...orchestration,
    runState: recordGenerationResult(orchestration.runState, update),
  };
}

export function finalizeGenerationCycle(
  orchestration: GenerationLoopOrchestration,
  phaseState: GenerationPhaseState,
  payload: {
    runId: string;
    generation: number;
    meanScore: number;
    bestScore: number;
    elo: number;
    gateDecision: GenerationGateDecision;
  },
): GenerationLoopOrchestration {
  const cycleStateWithPhase = updateGenerationCyclePhase(
    orchestration.cycleState,
    phaseState,
  );

  return {
    ...orchestration,
    cycleState: completeGenerationCycle(cycleStateWithPhase),
    events: {
      generationCompleted: buildGenerationCompletedPayload(
        payload.runId,
        payload.generation,
        payload,
      ),
    },
  };
}

export function completeGenerationLoopRun(
  orchestration: GenerationLoopOrchestration,
  opts: {
    finishedAtMs: number;
    sessionReportPath: string;
    deadEndsFound: number;
  },
): GenerationLoopOrchestration {
  const runState = completeGenerationRun(orchestration.runState, {
    finishedAtMs: opts.finishedAtMs,
  });

  return {
    ...orchestration,
    runState,
    events: {
      runCompleted: buildRunCompletedPayload({
        runId: runState.runId,
        completedGenerations: orchestration.cycleState.completedGenerations,
        bestScore: runState.bestScore,
        currentElo: runState.currentElo,
        sessionReportPath: opts.sessionReportPath,
        deadEndsFound: opts.deadEndsFound,
      }),
    },
  };
}

export function failGenerationLoopRun(
  orchestration: GenerationLoopOrchestration,
  opts: { finishedAtMs: number; error: string },
): GenerationLoopOrchestration {
  const runState = failGenerationRun(orchestration.runState, {
    finishedAtMs: opts.finishedAtMs,
    error: opts.error,
  });

  return {
    ...orchestration,
    runState,
    events: {
      runFailed: buildRunFailedPayload(runState.runId, opts.error),
    },
  };
}
