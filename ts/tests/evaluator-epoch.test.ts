import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  areComparable,
  computeEvaluatorEpoch,
  resolveEpochRebaseline,
} from "../src/judge/evaluator-epoch.js";

const fixturePath = join(
  import.meta.dirname,
  "..",
  "..",
  "autocontext",
  "tests",
  "fixtures",
  "evaluator-epoch-cases.json",
);

describe("evaluator-epoch parity", () => {
  it("matches the shared fixture epoch ids byte-for-byte", () => {
    const cases = JSON.parse(readFileSync(fixturePath, "utf8")).cases as Array<{
      rubric_text: string;
      judge_provider: string;
      judge_model: string;
      expected_epoch_id: string;
    }>;
    expect(cases.length).toBeGreaterThanOrEqual(4);
    for (const c of cases) {
      const epoch = computeEvaluatorEpoch(c.rubric_text, c.judge_provider, c.judge_model);
      expect(epoch.epochId).toBe(c.expected_epoch_id);
    }
  });

  it("are_comparable null semantics", () => {
    expect(areComparable("x", "x")).toBe(true);
    expect(areComparable("x", "y")).toBe(false);
    expect(areComparable(null, null)).toBe(true);
    expect(areComparable(null, "x")).toBe(false);
  });
});

describe("resolveEpochRebaseline", () => {
  it("never re-baselines the first round", () => {
    expect(resolveEpochRebaseline(null, "e1", false)).toEqual({
      rebaseline: false,
      staleEpoch: null,
    });
  });

  it("does not re-baseline when the round epoch matches the baseline", () => {
    expect(resolveEpochRebaseline("e1", "e1", true)).toEqual({
      rebaseline: false,
      staleEpoch: null,
    });
  });

  it("re-baselines and reports the stale epoch when the round epoch differs", () => {
    expect(resolveEpochRebaseline("e1", "e2", true)).toEqual({
      rebaseline: true,
      staleEpoch: "e1",
    });
  });
});
