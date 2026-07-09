import { describe, expect, it } from "vitest";

import { computeRubricSnapshot } from "../src/analytics/rubric-drift-statistics.js";
import type { RunFacetLike } from "../src/analytics/rubric-drift-types.js";

function facet(bestScore: number, evaluatorEpoch: string): RunFacetLike {
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
});
