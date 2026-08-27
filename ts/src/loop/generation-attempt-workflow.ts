import type { TournamentOpts, TournamentResult } from "../execution/tournament.js";
import type { CompletionResult } from "../types/index.js";
import {
  awaitGenerationCompetitorResult,
  awaitGenerationTournamentResult,
  finalizeGenerationAttemptDecision,
  type GenerationAttemptOrchestration,
} from "./generation-attempt-orchestrator.js";
import type { GenerationGateDecision } from "./generation-attempt-state.js";
import {
  buildGenerationAttemptCandidate,
  createTournamentExecutionPlan,
  parseCompetitorStrategyResult,
} from "./generation-execution-step.js";
import {
  executeRoleCompletionSideEffect,
  executeTournamentSideEffect,
  type GenerationLoopEventSequenceItem,
} from "./generation-side-effect-coordinator.js";
import { buildGenerationTournamentStartedEvent } from "./generation-tournament-event-sequencing.js";

export interface GenerationAttemptWorkflow {
  attemptOrchestration: GenerationAttemptOrchestration;
  runId: string;
  generation: number;
  competitorPrompt: string;
  seedBase: number;
  matchesPerGeneration: number;
  currentElo: number;
  executeCompetitor: () => Promise<CompletionResult>;
  roleMetadata?: {
    provider?: string;
    model?: string;
    inputBytes?: number;
  };
  onEvent?: (event: GenerationLoopEventSequenceItem) => void;
  beforeTournament?: () => Promise<void>;
  executeTournament: (input: {
    strategy: Record<string, unknown>;
    tournamentOptions: TournamentOpts;
  }) => TournamentResult;
  decideGate: (input: {
    attemptOrchestration: GenerationAttemptOrchestration;
    tournamentResult: TournamentResult;
  }) => {
    gateDecision: GenerationGateDecision;
    delta: number;
    threshold: number;
    metadata?: Record<string, unknown>;
  };
}

export function createGenerationAttemptWorkflow(
  workflow: GenerationAttemptWorkflow,
): GenerationAttemptWorkflow {
  return workflow;
}

export async function runGenerationAttemptWorkflow(
  workflow: GenerationAttemptWorkflow,
): Promise<{
  attemptOrchestration: GenerationAttemptOrchestration;
  competitorResult: CompletionResult;
  tournamentResult: TournamentResult;
  attempt: ReturnType<typeof buildGenerationAttemptCandidate>;
  events: GenerationLoopEventSequenceItem[];
}> {
  const events: GenerationLoopEventSequenceItem[] = [];
  const publish = (event: GenerationLoopEventSequenceItem) => {
    events.push(event);
    workflow.onEvent?.(event);
  };
  const attemptNumber = workflow.attemptOrchestration.phaseState.attemptState.retryCount + 1;
  let attemptOrchestration = awaitGenerationCompetitorResult(
    workflow.attemptOrchestration,
  );

  publish({
    event: "role_started",
    payload: {
      run_id: workflow.runId,
      generation: workflow.generation,
      role: "competitor",
      attempt: attemptNumber,
      ...(workflow.roleMetadata?.provider ? { provider: workflow.roleMetadata.provider } : {}),
      ...(workflow.roleMetadata?.model ? { model: workflow.roleMetadata.model } : {}),
      ...(workflow.roleMetadata?.inputBytes === undefined
        ? {}
        : { input_bytes: workflow.roleMetadata.inputBytes }),
    },
  });
  let competitorCompletion: Awaited<ReturnType<typeof executeRoleCompletionSideEffect>>;
  try {
    competitorCompletion = await executeRoleCompletionSideEffect({
      runId: workflow.runId,
      generation: workflow.generation,
      role: "competitor",
      execute: workflow.executeCompetitor,
      metadata: { ...workflow.roleMetadata, attempt: attemptNumber },
    });
  } catch (error) {
    publish({
      event: "role_failed",
      payload: {
        run_id: workflow.runId,
        generation: workflow.generation,
        role: "competitor",
        attempt: attemptNumber,
        status: "failed",
        reason: error instanceof Error ? error.name : "provider_failure",
      },
    });
    throw error;
  }
  const competitorResult = competitorCompletion.result;
  publish({ event: "role_completed", payload: competitorCompletion.roleCompletedPayload });
  const strategy = parseCompetitorStrategyResult(competitorResult.text);

  attemptOrchestration = awaitGenerationTournamentResult(attemptOrchestration);
  await workflow.beforeTournament?.();

  const tournamentPlan = createTournamentExecutionPlan({
    generation: workflow.generation,
    seedBase: workflow.seedBase,
    matchesPerGeneration: workflow.matchesPerGeneration,
    currentElo: workflow.currentElo,
  });
  publish(buildGenerationTournamentStartedEvent(
    workflow.runId,
    workflow.generation,
    workflow.matchesPerGeneration,
    attemptNumber,
  ));
  const tournamentExecution = executeTournamentSideEffect({
    runId: workflow.runId,
    generation: workflow.generation,
    scheduledMatches: workflow.matchesPerGeneration,
    executionPlan: tournamentPlan,
    strategy,
    executeTournament: workflow.executeTournament,
    attempt: attemptNumber,
  });
  for (const event of tournamentExecution.events.slice(1)) publish(event);

  const gateDecision = workflow.decideGate({
    attemptOrchestration,
    tournamentResult: tournamentExecution.tournamentResult,
  });
  const attempt = buildGenerationAttemptCandidate({
    competitorPrompt: workflow.competitorPrompt,
    competitorResultText: competitorResult.text,
    strategy,
    tournamentResult: tournamentExecution.tournamentResult,
    gateDecision: gateDecision.gateDecision,
  });
  attemptOrchestration = finalizeGenerationAttemptDecision(
    attemptOrchestration,
    {
      runId: workflow.runId,
      generation: workflow.generation,
      attempt,
      delta: gateDecision.delta,
      threshold: gateDecision.threshold,
      metadata: gateDecision.metadata,
    },
  );
  const gateEvent = {
    event: "gate_decided",
    payload: {
      ...attemptOrchestration.events.gateDecided!,
      attempt: attemptNumber,
    },
  } satisfies GenerationLoopEventSequenceItem;
  publish(gateEvent);

  return {
    attemptOrchestration,
    competitorResult,
    tournamentResult: tournamentExecution.tournamentResult,
    attempt,
    events,
  };
}
