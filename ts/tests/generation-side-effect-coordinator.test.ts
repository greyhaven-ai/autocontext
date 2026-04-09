import { describe, expect, it } from "vitest";

import type { TournamentOpts } from "../src/execution/tournament.js";
import {
  buildRoleCompletedPayload,
  executeRoleCompletionSideEffect,
  executeTournamentSideEffect,
  type GenerationLoopEventSequenceItem,
} from "../src/loop/generation-side-effect-coordinator.js";
import type { TournamentExecutionPlan } from "../src/loop/generation-execution-step.js";

describe("generation side-effect coordinator", () => {
  it("builds role completion payloads from mixed usage token formats", () => {
    expect(
      buildRoleCompletedPayload("competitor", 125, {
        input_tokens: 2,
        outputTokens: 5,
      }),
    ).toEqual({
      role: "competitor",
      latency_ms: 125,
      tokens: 7,
    });
  });

  it("executes role completion and reports timing metadata", async () => {
    const marks = [1000, 1145];

    const completed = await executeRoleCompletionSideEffect({
      role: "competitor",
      execute: async () => ({
        text: '{"aggression":0.7}',
        model: "test-model",
        usage: { inputTokens: 3, output_tokens: 4 },
      }),
      now: () => marks.shift() ?? 1145,
    });

    expect(completed.result.text).toBe('{"aggression":0.7}');
    expect(completed.roleCompletedPayload).toEqual({
      role: "competitor",
      latency_ms: 145,
      tokens: 7,
    });
  });

  it("executes tournament side effects using the prepared execution plan", () => {
    const executionPlan: TournamentExecutionPlan = {
      seedForGeneration: 1006,
      tournamentOptions: {
        matchCount: 3,
        seedBase: 1006,
        initialElo: 1040,
      },
    };
    const strategy = { aggression: 0.6 };
    const calls: Array<Record<string, unknown>> = [];

    const tournament = executeTournamentSideEffect({
      runId: "run-1",
      generation: 2,
      scheduledMatches: 3,
      executionPlan,
      strategy,
      executeTournament: ({
        strategy: nextStrategy,
        tournamentOptions,
      }: {
        strategy: Record<string, unknown>;
        tournamentOptions: TournamentOpts;
      }) => {
        calls.push({ strategy: nextStrategy, tournamentOptions });
        return {
          matches: [
            {
              seed: 1006,
              score: 0.75,
              winner: "challenger",
              passedValidation: true,
              validationErrors: [],
              replay: [],
            },
          ],
          meanScore: 0.75,
          bestScore: 0.75,
          wins: 1,
          losses: 0,
          elo: 1060,
        };
      },
    });

    expect(calls).toEqual([
      {
        strategy,
        tournamentOptions: executionPlan.tournamentOptions,
      },
    ]);
    expect(tournament.tournamentResult.bestScore).toBe(0.75);
    expect(tournament.events.map((event: GenerationLoopEventSequenceItem) => event.event)).toEqual([
      "tournament_started",
      "match_completed",
      "tournament_completed",
    ]);
  });
});
