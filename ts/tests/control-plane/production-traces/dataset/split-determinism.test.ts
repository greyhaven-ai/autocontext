import { describe, test, expect } from "vitest";
import fc from "fast-check";
import {
  partitionByRatios,
  partitionByRule,
  seededShuffle,
} from "../../../../src/production-traces/dataset/split.js";

describe("partitionByRatios", () => {
  test("exact partition — all items assigned, none dropped", () => {
    const items = Array.from({ length: 10 }, (_, i) => i);
    const out = partitionByRatios(items, { train: 0.6, eval: 0.2, holdout: 0.2 }, 42, false);
    expect(out.train.length + out.eval.length + out.holdout.length).toBe(10);
  });

  test("shuffle: false respects input order", () => {
    const items = [0, 1, 2, 3, 4];
    const out = partitionByRatios(items, { train: 0.6, eval: 0.2, holdout: 0.2 }, 0, false);
    expect(out.train).toEqual([0, 1, 2]);
    expect(out.eval).toEqual([3]);
    expect(out.holdout).toEqual([4]);
  });

  test("throws when ratios don't sum to 1.0", () => {
    expect(() =>
      partitionByRatios([1, 2, 3], { train: 0.5, eval: 0.5, holdout: 0.5 }, 0, false),
    ).toThrow(/sum to 1/);
  });

  test("throws on negative ratios", () => {
    expect(() =>
      partitionByRatios([1, 2, 3], { train: -0.1, eval: 0.5, holdout: 0.6 }, 0, false),
    ).toThrow(/non-negative/);
  });

  test("empty input produces three empty partitions", () => {
    const out = partitionByRatios([], { train: 0.7, eval: 0.15, holdout: 0.15 }, 0, true);
    expect(out.train.length).toBe(0);
    expect(out.eval.length).toBe(0);
    expect(out.holdout.length).toBe(0);
  });
});

describe("P2: split determinism (same seed + same ordering → same partitions)", () => {
  test("property: identical partitions over 100 runs", () => {
    fc.assert(
      fc.property(
        fc.array(fc.integer({ min: 0, max: 999 }), { minLength: 0, maxLength: 50 }),
        fc.integer({ min: 0, max: 2 ** 31 - 1 }),
        (items, seed) => {
          const a = partitionByRatios(items, { train: 0.7, eval: 0.15, holdout: 0.15 }, seed, true);
          const b = partitionByRatios(items, { train: 0.7, eval: 0.15, holdout: 0.15 }, seed, true);
          return (
            JSON.stringify(a.train) === JSON.stringify(b.train) &&
            JSON.stringify(a.eval) === JSON.stringify(b.eval) &&
            JSON.stringify(a.holdout) === JSON.stringify(b.holdout)
          );
        },
      ),
      { numRuns: 100 },
    );
  });

  test("different seeds produce different partitions (usually)", () => {
    // 10 distinct items with seeds 1 vs 2 — extremely unlikely to yield identical shuffle.
    const items = Array.from({ length: 10 }, (_, i) => i);
    const a = partitionByRatios(items, { train: 0.7, eval: 0.15, holdout: 0.15 }, 1, true);
    const b = partitionByRatios(items, { train: 0.7, eval: 0.15, holdout: 0.15 }, 2, true);
    const aAll = [...a.train, ...a.eval, ...a.holdout];
    const bAll = [...b.train, ...b.eval, ...b.holdout];
    expect(aAll).not.toEqual(bAll);
  });
});

describe("seededShuffle", () => {
  test("same seed + same input → identical output", () => {
    const items = [1, 2, 3, 4, 5];
    expect(seededShuffle(items, 42)).toEqual(seededShuffle(items, 42));
  });

  test("does not mutate input", () => {
    const items = [1, 2, 3];
    const before = items.slice();
    seededShuffle(items, 42);
    expect(items).toEqual(before);
  });
});

describe("partitionByRule", () => {
  test("reads ratios + seed + shuffle from the rule", () => {
    const items = [0, 1, 2, 3, 4];
    const out = partitionByRule(items, {
      type: "split",
      train: 0.6,
      eval: 0.2,
      holdout: 0.2,
      shuffle: false,
      seed: 0,
    });
    expect(out.train).toEqual([0, 1, 2]);
  });
});
