import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { areComparable, computeEvaluatorEpoch } from "../src/judge/evaluator-epoch.js";

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
