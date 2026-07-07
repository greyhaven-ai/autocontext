import { describe, test, expect } from "vitest";
import { validatePromotionScore } from "../../src/harness-optimization/contract/validators.js";
import type { PromotionScore } from "../../src/harness-optimization/contract/generated-types.js";

// A minimal fully-valid PromotionScore used as the fixture-of-record.
const validScore: PromotionScore = {
  schema_version: 1,
  candidate_id: "cand-001",
  weight_version: "w-2026-07",
  components: {
    dense_quality_score: 0.82,
    sparse_success_rate: 0.6,
    tokens_per_million: 1200,
    error_rate: 0.05,
    score_variance: 0.01,
  },
  weights: {
    sparse_success_weight: 1,
    token_cost_weight: 0.2,
    error_weight: 0.5,
    variance_weight: 0.1,
  },
  score: 0.71,
  parity: {
    python: "implemented",
    typescript: "pending",
    schema_hash: "abc123",
  },
};

describe("validatePromotionScore", () => {
  test("a fully-valid promotion score validates", () => {
    const result = validatePromotionScore(validScore);
    expect(result.valid).toBe(true);
  });

  test("a score missing a required component (error_rate) fails", () => {
    const { error_rate: _omitted, ...missingComponent } = validScore.components;
    const result = validatePromotionScore({ ...validScore, components: missingComponent });
    expect(result.valid).toBe(false);
    if (!result.valid) {
      expect(result.errors.join("\n")).toContain("error_rate");
    }
  });

  test("a score with an unknown extra field fails (additionalProperties)", () => {
    const withExtra = { ...validScore, surprise_field: "nope" };
    const result = validatePromotionScore(withExtra);
    expect(result.valid).toBe(false);
  });

  test("an empty parity object fails (python/typescript/schema_hash required)", () => {
    const emptyParity = { ...validScore, parity: {} };
    const result = validatePromotionScore(emptyParity);
    expect(result.valid).toBe(false);
  });

  test("a bad parity enum value fails", () => {
    const badEnum = {
      ...validScore,
      parity: { ...validScore.parity, python: "maybe" },
    };
    const result = validatePromotionScore(badEnum);
    expect(result.valid).toBe(false);
  });

  test("a sparse_success_rate above 1 fails (maximum 1)", () => {
    const outOfRange = {
      ...validScore,
      components: { ...validScore.components, sparse_success_rate: 1.5 },
    };
    const result = validatePromotionScore(outOfRange);
    expect(result.valid).toBe(false);
  });
});
