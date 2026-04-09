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

export interface GenerationAttemptWorkflow {
  attemptOrchestration: GenerationAttemptOrchestration;
  runId: string;
  generation: number;
  competitorPrompt: string;
  seedBase: number;
  matchesPerGeneration: number;
  currentElo: number;
  executeCompetitor: () => Promise<CompletionResult>;
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
  let attemptOrchestration = awaitGenerationCompetitorResult(
    workflow.attemptOrchestration,
  );

  const competitorCompletion = await executeRoleCompletionSideEffect({
    role: "competitor",
    execute: workflow.executeCompetitor,
  });
  const competitorResult = competitorCompletion.result;
  const strategy = parseCompetitorStrategyResult(competitorResult.text);

  attemptOrchestration = awaitGenerationTournamentResult(attemptOrchestration);
  await workflow.beforeTournament?.();

  const tournamentPlan = createTournamentExecutionPlan({
    generation: workflow.generation,
    seedBase: workflow.seedBase,
    matchesPerGeneration: workflow.matchesPerGeneration,
    currentElo: workflow.currentElo,
  });
  const tournamentExecution = executeTournamentSideEffect({
    runId: workflow.runId,
    generation: workflow.generation,
    scheduledMatches: workflow.matchesPerGeneration,
    executionPlan: tournamentPlan,
    strategy,
    executeTournament: workflow.executeTournament,
  });

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
    },
  );

  return {
    attemptOrchestration,
    competitorResult,
    tournamentResult: tournamentExecution.tournamentResult,
    attempt,
    events: [
      {
        event: "role_completed",
        payload: competitorCompletion.roleCompletedPayload,
      },
      ...tournamentExecution.events,
      {
        event: "gate_decided",
        payload: attemptOrchestration.events.gateDecided!,
      },
    ],
  };
}
