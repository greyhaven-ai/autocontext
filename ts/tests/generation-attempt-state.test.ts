import { describe, expect, it } from "vitest";

import {
  applyGenerationAttemptDecision,
  canContinueGenerationAttempt,
  createGenerationAttemptState,
  didAdvanceGenerationAttempt,
  getFinalizedGenerationAttempt,
  type GenerationAttempt,
} from "../src/loop/generation-attempt-state.js";

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

describe("generation attempt state", () => {
  it("starts with zero retries and no finalized attempt", () => {
    const state = createGenerationAttemptState({
      generation: 2,
      previousBestForGeneration: 0.4,
    });

    expect(state.generation).toBe(2);
    expect(state.retryCount).toBe(0);
    expect(state.finalizedAttempt).toBeNull();
    expect(canContinueGenerationAttempt(state, 2)).toBe(true);
  });

  it("increments retries without finalizing on retry decisions", () => {
    const started = createGenerationAttemptState({
      generation: 2,
      previousBestForGeneration: 0.4,
    });

    const retried = applyGenerationAttemptDecision(started, makeAttempt("retry", 0.41));

    expect(retried.retryCount).toBe(1);
    expect(retried.status).toBe("retrying");
    expect(retried.finalizedAttempt).toBeNull();
    expect(canContinueGenerationAttempt(retried, 0)).toBe(false);
    expect(canContinueGenerationAttempt(retried, 1)).toBe(true);
  });

  it("finalizes and marks advancement on advance decisions", () => {
    const started = createGenerationAttemptState({
      generation: 3,
      previousBestForGeneration: 0.45,
    });

    const advanced = applyGenerationAttemptDecision(started, makeAttempt("advance", 0.6));

    expect(advanced.status).toBe("advanced");
    expect(didAdvanceGenerationAttempt(advanced)).toBe(true);
    expect(getFinalizedGenerationAttempt(advanced).tournamentResult.bestScore).toBe(0.6);
    expect(canContinueGenerationAttempt(advanced, 2)).toBe(false);
  });

  it("finalizes rollback attempts without marking advancement", () => {
    const started = createGenerationAttemptState({
      generation: 4,
      previousBestForGeneration: 0.7,
    });

    const rolledBack = applyGenerationAttemptDecision(started, makeAttempt("rollback", 0.5));

    expect(rolledBack.status).toBe("rolled_back");
    expect(didAdvanceGenerationAttempt(rolledBack)).toBe(false);
    expect(getFinalizedGenerationAttempt(rolledBack).gateDecision).toBe("rollback");
  });
});
