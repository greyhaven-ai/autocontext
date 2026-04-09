import type { GenerationAttempt } from "./generation-attempt-state.js";
import { buildGateDecidedPayload } from "./generation-event-coordinator.js";
import {
  recordAdvancedGenerationResult,
  type GenerationLoopOrchestration,
} from "./generation-loop-orchestrator.js";
import {
  applyGenerationPhaseDecision,
  didAdvanceGenerationPhase,
  markAwaitingCompetitorResult,
  markAwaitingTournamentResult,
  type GenerationPhaseState,
} from "./generation-phase-state.js";
import { updateGenerationCyclePhase } from "./generation-cycle-state.js";

export interface GenerationAttemptOrchestration {
  orchestration: GenerationLoopOrchestration;
  phaseState: GenerationPhaseState;
  events: {
    gateDecided?: Record<string, unknown>;
  };
}

export function createGenerationAttemptOrchestration(
  orchestration: GenerationLoopOrchestration,
  phaseState: GenerationPhaseState,
): GenerationAttemptOrchestration {
  return {
    orchestration,
    phaseState,
    events: {},
  };
}

export function awaitGenerationCompetitorResult(
  attemptOrchestration: GenerationAttemptOrchestration,
): GenerationAttemptOrchestration {
  return withPhaseState(
    attemptOrchestration,
    markAwaitingCompetitorResult(attemptOrchestration.phaseState),
  );
}

export function awaitGenerationTournamentResult(
  attemptOrchestration: GenerationAttemptOrchestration,
): GenerationAttemptOrchestration {
  return withPhaseState(
    attemptOrchestration,
    markAwaitingTournamentResult(attemptOrchestration.phaseState),
  );
}

export function finalizeGenerationAttemptDecision(
  attemptOrchestration: GenerationAttemptOrchestration,
  opts: {
    runId: string;
    generation: number;
    attempt: GenerationAttempt;
    delta: number;
    threshold: number;
  },
): GenerationAttemptOrchestration {
  let next = withPhaseState(
    attemptOrchestration,
    applyGenerationPhaseDecision(attemptOrchestration.phaseState, opts.attempt),
  );

  if (didAdvanceGenerationPhase(next.phaseState)) {
    next = {
      ...next,
      orchestration: recordAdvancedGenerationResult(next.orchestration, {
        generation: opts.generation,
        bestScore: opts.attempt.tournamentResult.bestScore,
        elo: opts.attempt.tournamentResult.elo,
      }),
    };
  }

  return {
    ...next,
    events: {
      gateDecided: buildGateDecidedPayload(
        opts.runId,
        opts.generation,
        opts.attempt.gateDecision,
        opts.delta,
        opts.threshold,
      ),
    },
  };
}

function withPhaseState(
  attemptOrchestration: GenerationAttemptOrchestration,
  phaseState: GenerationPhaseState,
): GenerationAttemptOrchestration {
  return {
    ...attemptOrchestration,
    phaseState,
    orchestration: {
      ...attemptOrchestration.orchestration,
      cycleState: updateGenerationCyclePhase(
        attemptOrchestration.orchestration.cycleState,
        phaseState,
      ),
    },
  };
}
