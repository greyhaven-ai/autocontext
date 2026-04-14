import { describe, expect, it } from "vitest";
import {
  normalizeSimulationDelta,
  normalizeSimulationScore,
  normalizeSimulationSweepValue,
} from "../src/simulation/score-normalization.js";
import { aggregateSimulationRuns } from "../src/simulation/summary.js";

describe("simulation score normalization", () => {
  it("normalizes simulation scores and deltas to four decimals", () => {
    expect(normalizeSimulationScore(0.33335)).toBe(0.3333);
    expect(normalizeSimulationScore(0.33336)).toBe(0.3334);
    expect(normalizeSimulationDelta(0.111149)).toBe(0.1111);
    expect(normalizeSimulationDelta(-0.111151)).toBe(-0.1112);
  });

  it("normalizes sweep numeric values to four decimals", () => {
    expect(normalizeSimulationSweepValue(0.30000000004)).toBe(0.3);
    expect(normalizeSimulationSweepValue(0.6666666667)).toBe(0.6667);
  });

  it("uses normalized scores when aggregating repeated simulation runs", () => {
    const summary = aggregateSimulationRuns([
      { score: 0.33334, reasoning: "a", dimensionScores: { completion: 0.3 } },
      { score: 0.33336, reasoning: "b", dimensionScores: { completion: 0.4 } },
    ]);

    expect(summary.score).toBe(0.3334);
  });
});
