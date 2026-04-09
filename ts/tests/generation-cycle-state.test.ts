import { describe, expect, it } from "vitest";

import {
  completeGenerationCycle,
  createGenerationCycleState,
  getActiveGenerationPhaseState,
  hasRemainingGenerationCycles,
  startNextGenerationCycle,
  updateGenerationCyclePhase,
} from "../src/loop/generation-cycle-state.js";
import {
  applyGenerationPhaseDecision,
  markAwaitingCompetitorResult,
  markAwaitingTournamentResult,
  type GenerationAttempt,
} from "../src/loop/generation-phase-state.js";

function makeAttempt(
  gateDecision: GenerationAttempt["gateDecision"],
  bestScore: number,
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
      elo: 1000 + bestScore * 10,
    },
    gateDecision,
  };
}

describe("generation cycle state", () => {
  it("starts with remaining work and no active generation", () => {
    const state = createGenerationCycleState({ targetGenerations: 3 });

    expect(state.targetGenerations).toBe(3);
    expect(state.completedGenerations).toBe(0);
    expect(state.previousBestOverall).toBe(0);
    expect(state.activeGeneration).toBeNull();
    expect(hasRemainingGenerationCycles(state)).toBe(true);
  });

  it("starts the next generation using the previous best as context", () => {
    const started = startNextGenerationCycle(
      createGenerationCycleState({ targetGenerations: 3 }),
    );

    expect(started.activeGeneration?.generation).toBe(1);
    expect(started.activeGeneration?.previousBestForGeneration).toBe(0);

    const afterCompletion = completeGenerationCycle(
      updateGenerationCyclePhase(
        started,
        applyGenerationPhaseDecision(
          markAwaitingTournamentResult(
            markAwaitingCompetitorResult(getActiveGenerationPhaseState(started)),
          ),
          makeAttempt("advance", 0.62),
        ),
      ),
    );

    const second = startNextGenerationCycle(afterCompletion);
    expect(second.activeGeneration?.generation).toBe(2);
    expect(second.activeGeneration?.previousBestForGeneration).toBe(0.62);
  });

  it("completes generations and preserves best score across rollbacks", () => {
    const firstStarted = startNextGenerationCycle(
      createGenerationCycleState({ targetGenerations: 2 }),
    );
    const firstCompleted = completeGenerationCycle(
      updateGenerationCyclePhase(
        firstStarted,
        applyGenerationPhaseDecision(
          markAwaitingTournamentResult(
            markAwaitingCompetitorResult(getActiveGenerationPhaseState(firstStarted)),
          ),
          makeAttempt("advance", 0.7),
        ),
      ),
    );

    const secondStarted = startNextGenerationCycle(firstCompleted);
    const secondCompleted = completeGenerationCycle(
      updateGenerationCyclePhase(
        secondStarted,
        applyGenerationPhaseDecision(
          markAwaitingTournamentResult(
            markAwaitingCompetitorResult(getActiveGenerationPhaseState(secondStarted)),
          ),
          makeAttempt("rollback", 0.4),
        ),
      ),
    );

    expect(secondCompleted.completedGenerations).toBe(2);
    expect(secondCompleted.previousBestOverall).toBe(0.7);
    expect(hasRemainingGenerationCycles(secondCompleted)).toBe(false);
  });
});
