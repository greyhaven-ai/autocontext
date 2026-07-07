import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, test, expect } from "vitest";
import { validateCandidateEvidence } from "../../src/harness-optimization/contract/validators.js";
import type { CandidateEvidence } from "../../src/harness-optimization/contract/generated-types.js";

// Walk up to the repo root: ts/tests/harness-optimization/ -> ts/tests/ -> ts/ -> <repo root>.
const FIXTURES_DIR = join(
  import.meta.dirname,
  "..",
  "..",
  "..",
  "fixtures",
  "harness-optimization",
  "candidate-evidence",
);

// Both the Python and TypeScript packages load these exact same repo-root files.
const EXPECTED_FIXTURES = new Set([
  "valid-minimal.json",
  "valid-full.json",
  "invalid-missing-hypothesis.json",
  "invalid-bad-mechanism-type.json",
  "invalid-extra-field.json",
  "invalid-empty-parity.json",
]);

function fixtures(prefix: string): string[] {
  return readdirSync(FIXTURES_DIR)
    .filter((name) => name.startsWith(prefix) && name.endsWith(".json"))
    .sort();
}

function loadFixture(name: string): unknown {
  return JSON.parse(readFileSync(join(FIXTURES_DIR, name), "utf8"));
}

// A minimal fully-valid CandidateEvidence used as the fixture-of-record.
const validCandidate: CandidateEvidence = {
  schema_version: 1,
  candidate_id: "cand-001",
  mechanism_name: "tighten-validator",
  mechanism_type: "deterministic_code",
  target_surface: "harness_validator",
  hypothesis: "Requiring the target theorem to be present catches false-pass truncations.",
  changes: "Add a post-compile check that the named theorem exists in the output.",
  validation_plan: "Re-run the divergence seed and confirm the empty-file case now fails.",
  parity: {
    python: "implemented",
    typescript: "pending",
    schema_hash: "abc123",
  },
};

describe("validateCandidateEvidence", () => {
  test("a fully-valid candidate validates", () => {
    const result = validateCandidateEvidence(validCandidate);
    expect(result.valid).toBe(true);
  });

  test("a candidate missing a required field (hypothesis) fails", () => {
    const { hypothesis: _omitted, ...missingHypothesis } = validCandidate;
    const result = validateCandidateEvidence(missingHypothesis);
    expect(result.valid).toBe(false);
    if (!result.valid) {
      expect(result.errors.join("\n")).toContain("hypothesis");
    }
  });

  test("a candidate with an unknown extra field fails (additionalProperties)", () => {
    const withExtra = { ...validCandidate, surprise_field: "nope" };
    const result = validateCandidateEvidence(withExtra);
    expect(result.valid).toBe(false);
  });

  test("a bad mechanism_type enum value fails", () => {
    const badEnum = { ...validCandidate, mechanism_type: "not_a_mechanism" };
    const result = validateCandidateEvidence(badEnum);
    expect(result.valid).toBe(false);
  });
});

describe("shared repo-root candidate-evidence fixtures", () => {
  test.each(fixtures("valid-"))("%s validates", (name) => {
    const result = validateCandidateEvidence(loadFixture(name));
    expect(result.valid).toBe(true);
  });

  test.each(fixtures("invalid-"))("%s fails validation", (name) => {
    const result = validateCandidateEvidence(loadFixture(name));
    expect(result.valid).toBe(false);
  });

  test("the fixtures directory contains exactly the expected set", () => {
    // Set-membership guard: a dropped or renamed fixture fails here.
    const names = new Set(readdirSync(FIXTURES_DIR).filter((n) => n.endsWith(".json")));
    expect(names).toEqual(EXPECTED_FIXTURES);
  });
});
