import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, test, expect } from "vitest";
import { validatePromotionScore } from "../../src/harness-optimization/contract/validators.js";
import type { PromotionScore } from "../../src/harness-optimization/contract/generated-types.js";

// Walk up to the repo root: ts/tests/harness-optimization/ -> ts/tests/ -> ts/ -> <repo root>.
const FIXTURES_DIR = join(
  import.meta.dirname,
  "..",
  "..",
  "..",
  "fixtures",
  "harness-optimization",
  "promotion-score",
);

// The contract fixtures validated by both packages. score-cases.json also lives in
// this directory (the SCORER numeric fixture) and is deliberately excluded here: it
// matches neither the valid- nor invalid- prefix.
const EXPECTED_FIXTURES = new Set([
  "valid-full.json",
  "valid-minimal.json",
  "invalid-missing-component.json",
  "invalid-empty-parity.json",
  "invalid-extra-field.json",
]);

function fixtures(prefix: string): string[] {
  return readdirSync(FIXTURES_DIR)
    .filter((name) => name.startsWith(prefix) && name.endsWith(".json"))
    .sort();
}

function loadFixture(name: string): unknown {
  return JSON.parse(readFileSync(join(FIXTURES_DIR, name), "utf8"));
}

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

describe("shared repo-root promotion-score fixtures", () => {
  test.each(fixtures("valid-"))("%s validates", (name) => {
    const result = validatePromotionScore(loadFixture(name));
    expect(result.valid).toBe(true);
  });

  test.each(fixtures("invalid-"))("%s fails validation", (name) => {
    const result = validatePromotionScore(loadFixture(name));
    expect(result.valid).toBe(false);
  });

  test("the fixtures directory contains exactly the expected file set", () => {
    // Set-membership guard over EVERY .json in the directory: the five valid-/invalid-
    // contract fixtures plus score-cases.json (the scorer numeric fixture). Globbing all
    // files (not just the valid-/invalid- prefixes) means a stray or dropped .json fails
    // here instead of being silently ignored.
    const allJson = new Set(readdirSync(FIXTURES_DIR).filter((name) => name.endsWith(".json")));
    expect(allJson).toEqual(new Set([...EXPECTED_FIXTURES, "score-cases.json"]));
  });
});
