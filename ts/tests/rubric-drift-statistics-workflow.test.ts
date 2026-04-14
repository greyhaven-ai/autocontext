import { describe, expect, it } from "vitest";

import {
  computeRubricSnapshot,
  mean,
  median,
  populationStddev,
  syntheticTimestamp,
} from "../src/analytics/rubric-drift-statistics.js";

describe("rubric drift statistics workflow", () => {
  it("computes mean, median, stddev, and synthetic timestamps", () => {
    expect(mean([1, 2, 3])).toBe(2);
    expect(median([1, 3, 2, 4])).toBe(2.5);
    expect(populationStddev([1, 1, 1])).toBe(0);
    expect(syntheticTimestamp(2)).toContain("2026-01-01T00:00:02.000Z");
  });

  it("builds a rubric snapshot with score and retry aggregates", () => {
    const snapshot = computeRubricSnapshot([
      {
        scenario: "grid_ctf",
        bestScore: 0.5,
        createdAt: "2026-01-01T00:00:00Z",
        totalGenerations: 2,
        delightSignals: [],
        retries: 0,
        rollbacks: 0,
      },
      {
        scenario: "grid_ctf",
        bestScore: 0.9,
        createdAt: "2026-01-02T00:00:00Z",
        totalGenerations: 2,
        delightSignals: [{ signalType: "strong_improvement" }],
        retries: 1,
        rollbacks: 1,
      },
    ], { release: "0.3.7", scenarioFamily: "game", agentProvider: "anthropic" });

    expect(snapshot).toMatchObject({
      runCount: 2,
      meanScore: 0.7,
      release: "0.3.7",
      scenarioFamily: "game",
      agentProvider: "anthropic",
    });
    expect(snapshot.scoreInflationRate).toBe(0.4);
    expect(snapshot.revisionJumpRate).toBe(0.25);
    expect(snapshot.retryRate).toBe(0.25);
    expect(snapshot.rollbackRate).toBe(0.25);
  });
});
