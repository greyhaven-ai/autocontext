import { describe, test, expect } from "vitest";
import {
  defaultThresholds,
  computeConfidence,
} from "../../../src/control-plane/promotion/thresholds.js";

describe("defaultThresholds", () => {
  test("returns a complete PromotionThresholds with sensible defaults", () => {
    const t = defaultThresholds();
    expect(t.qualityMinDelta).toBeGreaterThan(0);
    expect(t.costMaxRelativeIncrease).toBeGreaterThan(0);
    expect(t.latencyMaxRelativeIncrease).toBeGreaterThan(0);
    expect(t.strongConfidenceMin).toBeGreaterThan(t.moderateConfidenceMin);
    expect(t.moderateConfidenceMin).toBeGreaterThan(0);
    expect(t.strongQualityMultiplier).toBeGreaterThanOrEqual(1);
  });

  test("two calls return equal threshold records (stable default)", () => {
    expect(defaultThresholds()).toEqual(defaultThresholds());
  });
});

describe("computeConfidence (log10 default)", () => {
  test("0 samples yields 0 confidence", () => {
    expect(computeConfidence(0)).toBe(0);
  });

  test("1000 samples yields 1.0 confidence", () => {
    expect(computeConfidence(1000)).toBe(1);
  });

  test("is monotonic in samples", () => {
    const values = [0, 1, 10, 100, 500, 1000].map(computeConfidence);
    for (let i = 1; i < values.length; i++) {
      expect(values[i]).toBeGreaterThanOrEqual(values[i - 1]);
    }
  });

  test("clamps above 1 for samples > 1000", () => {
    expect(computeConfidence(10000)).toBe(1);
    expect(computeConfidence(100000)).toBe(1);
  });

  test("100 samples ~ 0.67", () => {
    expect(computeConfidence(100)).toBeGreaterThan(0.6);
    expect(computeConfidence(100)).toBeLessThan(0.75);
  });

  test("negative samples yield 0", () => {
    expect(computeConfidence(-5)).toBe(0);
  });
});
