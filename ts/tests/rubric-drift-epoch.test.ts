import { describe, expect, it } from "vitest";

import { computeRubricSnapshot } from "../src/analytics/rubric-drift-statistics.js";
import { DEFAULT_THRESHOLDS, detectRubricDrift } from "../src/analytics/rubric-drift-warnings.js";
import type { RunFacetLike } from "../src/analytics/rubric-drift-types.js";

function facet(bestScore: number, evaluatorEpoch: string | null): RunFacetLike {
  return {
    scenario: "s",
    bestScore,
    totalGenerations: 1,
    delightSignals: [],
    retries: 0,
    rollbacks: 0,
    evaluatorEpoch,
  };
}

describe("rubric drift epoch lineage", () => {
  it("flags mixed epoch and carries evaluator epochs without changing the math", () => {
    const single = computeRubricSnapshot([facet(0.9, "e1"), facet(0.8, "e1")]);
    expect(single.mixedEpoch).toBe(false);
    expect(single.evaluatorEpochs).toEqual(["e1"]);

    const mixed = computeRubricSnapshot([facet(0.9, "e1"), facet(0.8, "e2")]);
    expect(mixed.mixedEpoch).toBe(true);
    expect(mixed.evaluatorEpochs).toEqual(["e1", "e2"]);

    // math unchanged: mean over the same scores regardless of epoch
    expect(mixed.meanScore).toBe(single.meanScore);
  });

  it("treats a known epoch mixed with null as mixed (None is its own class)", () => {
    const knownUnknown = computeRubricSnapshot([facet(0.9, "e1"), facet(0.8, null)]);
    expect(knownUnknown.mixedEpoch).toBe(true);
    expect(knownUnknown.hasUnknownEpoch).toBe(true);
    expect(knownUnknown.evaluatorEpochs).toEqual(["e1"]); // only KNOWN epochs displayed

    const allUnknown = computeRubricSnapshot([facet(0.9, null), facet(0.8, null)]);
    expect(allUnknown.mixedEpoch).toBe(false);
    expect(allUnknown.hasUnknownEpoch).toBe(true);
    expect(allUnknown.evaluatorEpochs).toEqual([]);
  });

  it("flags the baseline inflation warning when the two snapshots span different epochs", () => {
    const baseline = computeRubricSnapshot([facet(0.5, "e1"), facet(0.5, "e1")]);
    const currentE2 = computeRubricSnapshot([facet(0.9, "e2"), facet(0.9, "e2")]);
    const warnings = detectRubricDrift(currentE2, DEFAULT_THRESHOLDS, baseline);
    const inflation = warnings.filter((w) => w.metricName === "mean_score_delta");
    expect(inflation).toHaveLength(1);
    expect(currentE2.mixedEpoch).toBe(false); // current alone is single-epoch
    expect(inflation[0].mixedEpoch).toBe(true); // but the comparison spans e1 + e2

    const currentE1 = computeRubricSnapshot([facet(0.9, "e1"), facet(0.9, "e1")]);
    const warningsSame = detectRubricDrift(currentE1, DEFAULT_THRESHOLDS, baseline);
    const inflationSame = warningsSame.filter((w) => w.metricName === "mean_score_delta");
    expect(inflationSame).toHaveLength(1);
    expect(inflationSame[0].mixedEpoch).toBe(false);
  });
});
