import { describe, expect, it } from "vitest";

import {
  applyScoreDeltaPolicy,
  buildRevisionFeedbackResult,
  evaluatePlateauState,
  evaluateThresholdState,
} from "../src/execution/improvement-loop-policy.js";

describe("improvement loop policy workflow", () => {
  it("caps large score jumps, tracks plateau state, and evaluates threshold stability", () => {
    expect(applyScoreDeltaPolicy({
      score: 0.9,
      prevValidScore: 0.2,
      maxScoreDelta: 0.3,
      capScoreJumps: true,
      roundNum: 2,
    })).toEqual({
      effectiveScore: 0.5,
      warning: "Score jump of 0.700 exceeds maxScoreDelta 0.3 (round 2: 0.200 -> 0.900)",
    });

    expect(evaluatePlateauState({
      prevValidScore: 0.5,
      score: 0.505,
      plateauCount: 1,
      roundNum: 3,
      minRounds: 1,
    })).toEqual({ plateauCount: 2, shouldStop: true });

    expect(evaluateThresholdState({
      effectiveScore: 0.91,
      qualityThreshold: 0.9,
      roundNum: 1,
      minRounds: 1,
      maxRounds: 5,
      thresholdMetRound: null,
      dimensionScores: {},
      dimensionThreshold: null,
    })).toEqual({
      metThreshold: false,
      shouldStop: false,
      thresholdMetRound: 1,
    });
  });

  it("adds dimension annotations with regression and improvement notes", () => {
    const revisionFeedback = buildRevisionFeedbackResult({
      result: {
        score: 0.8,
        reasoning: "Needs improvement",
        dimensionScores: { clarity: 0.7, accuracy: 0.6 },
        internalRetries: 1,
      },
      previousValidRound: {
        roundNumber: 1,
        output: "draft",
        score: 0.75,
        reasoning: "prior",
        dimensionScores: { clarity: 0.8, accuracy: 0.4 },
        isRevision: false,
        judgeFailed: false,
      },
    });

    expect(revisionFeedback.reasoning).toContain("Dimension Scores:");
    expect(revisionFeedback.reasoning).toContain("clarity: 0.70 (REGRESSION from 0.80 -- preserve this dimension)");
    expect(revisionFeedback.reasoning).toContain("accuracy: 0.60 (improved from 0.40)");
  });
});
