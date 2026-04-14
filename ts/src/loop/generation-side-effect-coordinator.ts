import type { TournamentOpts, TournamentResult } from "../execution/tournament.js";
import type { CompletionResult } from "../types/index.js";
import type { GenerationRole } from "../providers/index.js";

export interface RoleCompletedPayload {
  [key: string]: unknown;
  role: "competitor" | "analyst" | "coach" | "curator";
  latency_ms: number;
  tokens: number;
}
import type { TournamentExecutionPlan } from "./generation-execution-step.js";
import {
  buildGenerationTournamentEventSequence,
  type GenerationLoopEventSequenceItem,
} from "./generation-tournament-event-sequencing.js";

export type { GenerationLoopEventSequenceItem };

export function buildRoleCompletedPayload(
  role: "competitor" | "analyst" | "coach" | "curator",
  latencyMs: number,
  usage: Record<string, number>,
): RoleCompletedPayload {
  const inputTokens = usage.input_tokens ?? usage.inputTokens ?? 0;
  const outputTokens = usage.output_tokens ?? usage.outputTokens ?? 0;

  return {
    role,
    latency_ms: latencyMs,
    tokens: inputTokens + outputTokens,
  };
}

export async function executeRoleCompletionSideEffect(opts: {
  role: "competitor" | "analyst" | "coach" | "curator";
  execute: () => Promise<CompletionResult>;
  now?: () => number;
}): Promise<{
  result: CompletionResult;
  roleCompletedPayload: RoleCompletedPayload;
}> {
  const now = opts.now ?? Date.now;
  const startedAt = now();
  const result = await opts.execute();
  const finishedAt = now();

  return {
    result,
    roleCompletedPayload: buildRoleCompletedPayload(
      opts.role,
      finishedAt - startedAt,
      result.usage,
    ),
  };
}

export function executeTournamentSideEffect(opts: {
  runId: string;
  generation: number;
  scheduledMatches: number;
  executionPlan: TournamentExecutionPlan;
  strategy: Record<string, unknown>;
  executeTournament: (input: {
    strategy: Record<string, unknown>;
    tournamentOptions: TournamentOpts;
  }) => TournamentResult;
}): {
  tournamentResult: TournamentResult;
  events: GenerationLoopEventSequenceItem[];
} {
  const tournamentResult = opts.executeTournament({
    strategy: opts.strategy,
    tournamentOptions: opts.executionPlan.tournamentOptions,
  });

  return {
    tournamentResult,
    events: buildGenerationTournamentEventSequence({
      runId: opts.runId,
      generation: opts.generation,
      scheduledMatches: opts.scheduledMatches,
      tournamentResult,
    }),
  };
}
