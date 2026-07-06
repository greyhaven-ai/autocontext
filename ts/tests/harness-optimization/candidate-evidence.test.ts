import { describe, test, expect } from "vitest";
import { validateCandidateEvidence } from "../../src/harness-optimization/contract/validators.js";
import type { CandidateEvidence } from "../../src/harness-optimization/contract/generated-types.js";

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
