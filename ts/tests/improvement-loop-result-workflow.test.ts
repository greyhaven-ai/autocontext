import { describe, expect, it } from "vitest";

import {
  buildImprovementResult,
  buildRoundResult,
} from "../src/execution/improvement-loop-result.js";

describe("improvement loop result workflow", () => {
  it("builds round results with worst-dimension tracking", () => {
    expect(buildRoundResult({
      roundNumber: 2,
      output: "revised draft",
      result: {
        score: 0.82,
        reasoning: "Better",
        dimensionScores: { clarity: 0.9, accuracy: 0.7, depth: 0.8 },
        internalRetries: 0,
      },
      judgeFailed: false,
      roundDurationMs: 12,
    })).toMatchObject({
      roundNumber: 2,
      isRevision: true,
      worstDimension: "accuracy",
      worstDimensionScore: 0.7,
      roundDurationMs: 12,
    });
  });

  it("assembles final improvement loop results", () => {
    const rounds = [buildRoundResult({
      roundNumber: 1,
      output: "draft",
      result: {
        score: 0.6,
        reasoning: "ok",
        dimensionScores: {},
        internalRetries: 0,
      },
      judgeFailed: false,
      roundDurationMs: 5,
    })];

    expect(buildImprovementResult({
      rounds,
      bestOutput: "draft",
      bestScore: 0.6,
      bestRound: 1,
      metThreshold: false,
      judgeFailures: 0,
      terminationReason: "max_rounds",
      dimensionTrajectory: {},
      totalInternalRetries: 0,
      durationMs: 20,
      judgeCalls: 1,
    })).toEqual({
      rounds,
      bestOutput: "draft",
      bestScore: 0.6,
      bestRound: 1,
      totalRounds: 1,
      metThreshold: false,
      judgeFailures: 0,
      terminationReason: "max_rounds",
      dimensionTrajectory: {},
      totalInternalRetries: 0,
      durationMs: 20,
      judgeCalls: 1,
    });
  });
});
