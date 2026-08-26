import { describe, expect, it } from "vitest";

import type { TournamentOpts } from "../src/execution/tournament.js";
import type { CompletionResult } from "../src/types/index.js";
import {
  createGenerationAttemptWorkflow,
  runGenerationAttemptWorkflow,
} from "../src/loop/generation-attempt-workflow.js";
import type { GenerationLoopEventSequenceItem } from "../src/loop/generation-side-effect-coordinator.js";
import {
  createGenerationLoopOrchestration,
  getActiveGenerationPhase,
  startNextGeneration,
} from "../src/loop/generation-loop-orchestrator.js";

describe("generation attempt workflow", () => {
  function createStartedWorkflow() {
    const orchestration = startNextGeneration(
      createGenerationLoopOrchestration({
        runId: "run-1",
        scenarioName: "grid_ctf",
        targetGenerations: 2,
        startedAtMs: 100,
      }),
      false,
    );

    return createGenerationAttemptWorkflow({
      attemptOrchestration: {
        orchestration,
        phaseState: getActiveGenerationPhase(orchestration),
        events: {},
      },
      runId: "run-1",
      generation: 1,
      competitorPrompt: "prompt",
      seedBase: 1000,
      matchesPerGeneration: 2,
      currentElo: 1000,
      executeCompetitor: async (): Promise<CompletionResult> => ({
        text: '{"aggression":0.7}',
        model: "test-model",
        usage: { inputTokens: 3, output_tokens: 4 },
      }),
      executeTournament: ({
        strategy,
        tournamentOptions,
      }: {
        strategy: Record<string, unknown>;
        tournamentOptions: TournamentOpts;
      }) => ({
        matches: [
          {
            seed: tournamentOptions.seedBase,
            score: Number(strategy.aggression ?? 0),
            winner: "challenger",
            passedValidation: true,
            validationErrors: [],
            replay: [],
          },
        ],
        meanScore: Number(strategy.aggression ?? 0),
        bestScore: Number(strategy.aggression ?? 0),
        wins: 1,
        losses: 0,
        elo: 1015,
      }),
      decideGate: () => ({
        gateDecision: "retry",
        delta: 0.001,
        threshold: 0.005,
      }),
    });
  }

  it("runs a retrying attempt workflow and preserves run score state", async () => {
    const workflow = await runGenerationAttemptWorkflow(createStartedWorkflow());

    expect(workflow.attemptOrchestration.phaseState.phase).toBe("gate_decided");
    expect(workflow.attemptOrchestration.orchestration.runState.bestScore).toBe(0);
    expect(workflow.events.map((event: GenerationLoopEventSequenceItem) => event.event)).toEqual([
      "role_completed",
      "tournament_started",
      "match_completed",
      "tournament_completed",
      "gate_decided",
    ]);
    expect(workflow.events.at(-1)?.payload).toEqual({
      run_id: "run-1",
      generation: 1,
      decision: "retry",
      delta: 0.001,
      threshold: 0.005,
    });
  });

  it("runs an advancing attempt workflow and records run progress", async () => {
    const workflow = await runGenerationAttemptWorkflow(
      createGenerationAttemptWorkflow({
        ...createStartedWorkflow(),
        decideGate: () => ({
          gateDecision: "advance",
          delta: 0.2,
          threshold: 0.005,
        }),
      }),
    );

    expect(workflow.attemptOrchestration.phaseState.phase).toBe("finalized");
    expect(workflow.attemptOrchestration.orchestration.runState.bestScore).toBe(0.7);
    expect(workflow.attemptOrchestration.orchestration.runState.currentElo).toBe(1015);
    expect(workflow.attempt.tournamentResult.bestScore).toBe(0.7);
    expect(workflow.events.map((event: GenerationLoopEventSequenceItem) => event.event)).toEqual([
      "role_completed",
      "tournament_started",
      "match_completed",
      "tournament_completed",
      "gate_decided",
    ]);
  });

  it("repairs malformed competitor JSON once before tournament evaluation", async () => {
    let tournamentStrategy: Record<string, unknown> | null = null;
    const repairCompetitor = async ({
      repairPrompt,
      invalidOutput,
    }: {
      repairPrompt: string;
      invalidOutput: string;
    }): Promise<CompletionResult> => {
      expect(invalidOutput).toBe("not-json");
      expect(repairPrompt).toContain("Repair the invalid competitor strategy");
      return {
        text: '{"aggression":0.8,"defense":0.4,"path_bias":0.3}',
        model: "test-model",
        usage: {},
      };
    };

    const result = await runGenerationAttemptWorkflow(
      createGenerationAttemptWorkflow({
        ...createStartedWorkflow(),
        executeCompetitor: async () => ({
          text: "not-json",
          model: "test-model",
          usage: {},
        }),
        repairCompetitor,
        executeTournament: ({ strategy }) => {
          tournamentStrategy = strategy;
          return {
            matches: [],
            meanScore: 0.8,
            bestScore: 0.8,
            wins: 1,
            losses: 0,
            elo: 1015,
          };
        },
      }),
    );

    expect(tournamentStrategy).toEqual({
      aggression: 0.8,
      defense: 0.4,
      path_bias: 0.3,
    });
    expect(result.competitorResult.text).toContain('"aggression":0.8');
    expect(result.events.filter((event) => event.event === "role_completed")).toHaveLength(2);
  });

  it("fails before tournament evaluation when the bounded repair is still invalid", async () => {
    let repairCalls = 0;
    let tournamentCalls = 0;
    await expect(
      runGenerationAttemptWorkflow(
        createGenerationAttemptWorkflow({
          ...createStartedWorkflow(),
          executeCompetitor: async () => ({
            text: "not-json",
            model: "test-model",
            usage: {},
          }),
          repairCompetitor: async () => {
            repairCalls += 1;
            return { text: "still-not-json", model: "test-model", usage: {} };
          },
          executeTournament: (input) => {
            tournamentCalls += 1;
            return createStartedWorkflow().executeTournament(input);
          },
        }),
      ),
    ).rejects.toThrow(
      "invalid strategy JSON after one repair attempt; generation was not evaluated",
    );
    expect(repairCalls).toBe(1);
    expect(tournamentCalls).toBe(0);
  });
});
