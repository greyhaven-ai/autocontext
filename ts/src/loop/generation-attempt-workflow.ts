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
  buildCompetitorStrategyRepairPrompt,
  buildGenerationAttemptCandidate,
  CompetitorStrategyParseError,
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
  repairCompetitor?: (input: {
    repairPrompt: string;
    invalidOutput: string;
  }) => Promise<CompletionResult>;
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

export async function runGenerationAttemptWorkflow(workflow: GenerationAttemptWorkflow): Promise<{
  attemptOrchestration: GenerationAttemptOrchestration;
  competitorResult: CompletionResult;
  tournamentResult: TournamentResult;
  attempt: ReturnType<typeof buildGenerationAttemptCandidate>;
  events: GenerationLoopEventSequenceItem[];
}> {
  let attemptOrchestration = awaitGenerationCompetitorResult(workflow.attemptOrchestration);

  let competitorCompletion = await executeRoleCompletionSideEffect({
    runId: workflow.runId,
    generation: workflow.generation,
    role: "competitor",
    execute: workflow.executeCompetitor,
  });
  const competitorEvents: GenerationLoopEventSequenceItem[] = [
    {
      event: "role_completed",
      payload: competitorCompletion.roleCompletedPayload,
    },
  ];
  let competitorResult = competitorCompletion.result;
  let strategy: Record<string, unknown>;
  try {
    strategy = parseCompetitorStrategyResult(competitorResult.text);
  } catch (error) {
    const repairCompetitor = workflow.repairCompetitor;
    if (!repairCompetitor) {
      throw error;
    }
    const invalidOutput = competitorResult.text;
    const repairPrompt = buildCompetitorStrategyRepairPrompt({
      competitorPrompt: workflow.competitorPrompt,
      invalidOutput,
    });
    competitorCompletion = await executeRoleCompletionSideEffect({
      runId: workflow.runId,
      generation: workflow.generation,
      role: "competitor",
      execute: () =>
        repairCompetitor({
          repairPrompt,
          invalidOutput,
        }),
    });
    competitorEvents.push({
      event: "role_completed",
      payload: competitorCompletion.roleCompletedPayload,
    });
    competitorResult = competitorCompletion.result;
    try {
      strategy = parseCompetitorStrategyResult(competitorResult.text);
    } catch {
      throw new CompetitorStrategyParseError(
        "Competitor returned invalid strategy JSON after one repair attempt; generation was not evaluated",
      );
    }
  }

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
  attemptOrchestration = finalizeGenerationAttemptDecision(attemptOrchestration, {
    runId: workflow.runId,
    generation: workflow.generation,
    attempt,
    delta: gateDecision.delta,
    threshold: gateDecision.threshold,
    metadata: gateDecision.metadata,
  });

  return {
    attemptOrchestration,
    competitorResult,
    tournamentResult: tournamentExecution.tournamentResult,
    attempt,
    events: [
      ...competitorEvents,
      ...tournamentExecution.events,
      {
        event: "gate_decided",
        payload: attemptOrchestration.events.gateDecided!,
      },
    ],
  };
}
