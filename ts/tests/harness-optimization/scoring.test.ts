import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, test, expect } from "vitest";
import {
  harnessPromotionScore,
  beatsIncumbent,
  type Components,
  type Weights,
} from "../../src/harness-optimization/scoring.js";

// Load the SAME repo-root fixture the Python suite loads. Matching it in both
// languages is the load-bearing parity proof.
// Walk up to the repo root: ts/tests/harness-optimization/ -> ts/tests/ -> ts/ -> <repo root>.
const FIXTURE = join(
  import.meta.dirname,
  "..",
  "..",
  "..",
  "fixtures",
  "harness-optimization",
  "promotion-score",
  "score-cases.json",
);

interface ScoreCase {
  name: string;
  components: Components;
  weights: Weights;
  expected_score: number;
}

interface BeatsCase {
  name: string;
  challenger: Components;
  incumbent: Components;
  weights: Weights;
  min_margin: number;
  expected_beats: boolean;
}

const cases = JSON.parse(readFileSync(FIXTURE, "utf8")) as {
  score_cases: ScoreCase[];
  beats_cases: BeatsCase[];
};

const TOL = 1e-9;

describe("harnessPromotionScore matches the shared fixture", () => {
  for (const c of cases.score_cases) {
    test(c.name, () => {
      expect(harnessPromotionScore(c.components, c.weights)).toBeCloseTo(c.expected_score, 9);
      // toBeCloseTo(x, 9) is a decimal-places check; assert the absolute tolerance explicitly too.
      expect(
        Math.abs(harnessPromotionScore(c.components, c.weights) - c.expected_score),
      ).toBeLessThan(TOL);
    });
  }
});

describe("beatsIncumbent matches the shared fixture", () => {
  for (const c of cases.beats_cases) {
    test(c.name, () => {
      expect(beatsIncumbent(c.challenger, c.incumbent, c.weights, c.min_margin)).toBe(
        c.expected_beats,
      );
    });
  }
});
