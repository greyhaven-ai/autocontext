import { Buffer } from "node:buffer";

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
import { buildGenerationTournamentStartedEvent } from "./generation-tournament-event-sequencing.js";

export interface GenerationAttemptWorkflow {
  attemptOrchestration: GenerationAttemptOrchestration;
  runId: string;
  generation: number;
  competitorPrompt: string;
  strategyInterface: string;
  seedBase: number;
  matchesPerGeneration: number;
  currentElo: number;
  executeCompetitor: () => Promise<CompletionResult>;
  roleMetadata?: {
    provider?: string;
    model?: string;
    inputBytes?: number;
  };
  repairCompetitor?: (input: {
    repairPrompt: string;
    invalidOutput: string;
  }) => Promise<CompletionResult>;
  onEvent?: (item: GenerationLoopEventSequenceItem) => void;
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
  const events: GenerationLoopEventSequenceItem[] = [];
  const recordEvent = (item: GenerationLoopEventSequenceItem): void => {
    if (workflow.onEvent) {
      workflow.onEvent(item);
    } else {
      events.push(item);
    }
  };
  const attemptNumber = workflow.attemptOrchestration.phaseState.attemptState.retryCount + 1;
  let attemptOrchestration = awaitGenerationCompetitorResult(workflow.attemptOrchestration);

  const executeCompetitor = async (
    execute: () => Promise<CompletionResult>,
    inputBytes: number | undefined,
  ): Promise<Awaited<ReturnType<typeof executeRoleCompletionSideEffect>>> => {
    recordEvent({
      event: "role_started",
      payload: {
        run_id: workflow.runId,
        generation: workflow.generation,
        role: "competitor",
        attempt: attemptNumber,
        ...(workflow.roleMetadata?.provider
          ? { provider: workflow.roleMetadata.provider }
          : {}),
        ...(workflow.roleMetadata?.model ? { model: workflow.roleMetadata.model } : {}),
        ...(inputBytes === undefined ? {} : { input_bytes: inputBytes }),
      },
    });

    try {
      const completion = await executeRoleCompletionSideEffect({
        runId: workflow.runId,
        generation: workflow.generation,
        role: "competitor",
        execute,
        metadata: {
          ...workflow.roleMetadata,
          attempt: attemptNumber,
          ...(inputBytes === undefined ? {} : { inputBytes }),
        },
      });
      recordEvent({
        event: "role_completed",
        payload: completion.roleCompletedPayload,
      });
      return completion;
    } catch (error) {
      recordEvent({
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
  };

  let competitorCompletion = await executeCompetitor(
    workflow.executeCompetitor,
    workflow.roleMetadata?.inputBytes,
  );
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
      strategyInterface: workflow.strategyInterface,
      invalidOutput,
    });
    competitorCompletion = await executeCompetitor(
      () =>
        repairCompetitor({
          repairPrompt,
          invalidOutput,
        }),
      Buffer.byteLength(repairPrompt, "utf-8"),
    );
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
  recordEvent(
    buildGenerationTournamentStartedEvent(
      workflow.runId,
      workflow.generation,
      workflow.matchesPerGeneration,
      attemptNumber,
    ),
  );
  const tournamentExecution = executeTournamentSideEffect({
    runId: workflow.runId,
    generation: workflow.generation,
    scheduledMatches: workflow.matchesPerGeneration,
    executionPlan: tournamentPlan,
    strategy,
    executeTournament: workflow.executeTournament,
    attempt: attemptNumber,
  });
  for (const item of tournamentExecution.events.slice(1)) recordEvent(item);

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
  recordEvent(gateEvent);

  return {
    attemptOrchestration,
    competitorResult,
    tournamentResult: tournamentExecution.tournamentResult,
    attempt,
    events,
  };
}
