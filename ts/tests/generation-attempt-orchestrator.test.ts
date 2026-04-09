import { describe, expect, it } from "vitest";

import type { GenerationAttempt } from "../src/loop/generation-attempt-state.js";
import {
  awaitGenerationCompetitorResult,
  awaitGenerationTournamentResult,
  createGenerationAttemptOrchestration,
  finalizeGenerationAttemptDecision,
} from "../src/loop/generation-attempt-orchestrator.js";
import {
  createGenerationLoopOrchestration,
  getActiveGenerationPhase,
  startNextGeneration,
} from "../src/loop/generation-loop-orchestrator.js";

function makeAttempt(
  gateDecision: GenerationAttempt["gateDecision"],
  bestScore: number,
  elo = 1000 + bestScore * 100,
): GenerationAttempt {
  return {
    competitorPrompt: "prompt",
    competitorResultText: '{"aggression":0.5}',
    strategy: { aggression: 0.5 },
    tournamentResult: {
      matches: [],
      meanScore: bestScore,
      bestScore,
      wins: 1,
      losses: 0,
      elo,
    },
    gateDecision,
  };
}

describe("generation attempt orchestrator", () => {
  function createStartedAttemptOrchestration() {
    const orchestration = startNextGeneration(
      createGenerationLoopOrchestration({
        runId: "run-1",
        scenarioName: "grid_ctf",
        targetGenerations: 2,
        startedAtMs: 100,
      }),
      false,
    );

    return createGenerationAttemptOrchestration(
      orchestration,
      getActiveGenerationPhase(orchestration),
    );
  }

  it("marks the active generation as awaiting competitor result", () => {
    const attemptOrchestration = awaitGenerationCompetitorResult(
      createStartedAttemptOrchestration(),
    );

    expect(attemptOrchestration.phaseState.phase).toBe("awaiting_competitor_result");
    expect(
      attemptOrchestration.orchestration.cycleState.activeGeneration?.phase,
    ).toBe("awaiting_competitor_result");
  });

  it("marks the active generation as awaiting tournament result", () => {
    const attemptOrchestration = awaitGenerationTournamentResult(
      awaitGenerationCompetitorResult(createStartedAttemptOrchestration()),
    );

    expect(attemptOrchestration.phaseState.phase).toBe("awaiting_tournament_result");
    expect(
      attemptOrchestration.orchestration.cycleState.activeGeneration?.phase,
    ).toBe("awaiting_tournament_result");
  });

  it("applies retry decisions without advancing run results", () => {
    const attemptOrchestration = finalizeGenerationAttemptDecision(
      awaitGenerationTournamentResult(
        awaitGenerationCompetitorResult(createStartedAttemptOrchestration()),
      ),
      {
        runId: "run-1",
        generation: 1,
        attempt: makeAttempt("retry", 0.51, 1020),
        delta: 0.001,
        threshold: 0.005,
      },
    );

    expect(attemptOrchestration.phaseState.phase).toBe("gate_decided");
    expect(attemptOrchestration.phaseState.attemptState.retryCount).toBe(1);
    expect(attemptOrchestration.orchestration.runState.bestScore).toBe(0);
    expect(attemptOrchestration.events.gateDecided).toEqual({
      run_id: "run-1",
      generation: 1,
      decision: "retry",
      delta: 0.001,
      threshold: 0.005,
    });
  });

  it("applies advance decisions and records generation results", () => {
    const attemptOrchestration = finalizeGenerationAttemptDecision(
      awaitGenerationTournamentResult(
        awaitGenerationCompetitorResult(createStartedAttemptOrchestration()),
      ),
      {
        runId: "run-1",
        generation: 1,
        attempt: makeAttempt("advance", 0.72, 1088),
        delta: 0.22,
        threshold: 0.005,
      },
    );

    expect(attemptOrchestration.phaseState.phase).toBe("finalized");
    expect(attemptOrchestration.orchestration.runState.bestScore).toBe(0.72);
    expect(attemptOrchestration.orchestration.runState.currentElo).toBe(1088);
    expect(attemptOrchestration.events.gateDecided).toEqual({
      run_id: "run-1",
      generation: 1,
      decision: "advance",
      delta: 0.22,
      threshold: 0.005,
    });
  });
});
