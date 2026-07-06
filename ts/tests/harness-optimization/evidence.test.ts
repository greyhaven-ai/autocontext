import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, test, expect } from "vitest";
import {
  readCandidateEvidence,
  writeCandidateEvidence,
} from "../../src/harness-optimization/evidence.js";

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
const VALID_FULL = join(FIXTURES_DIR, "valid-full.json");
const INVALID_MISSING = join(FIXTURES_DIR, "invalid-missing-hypothesis.json");

describe("readCandidateEvidence / writeCandidateEvidence", () => {
  test("round-trips a full fixture through read -> write -> read", () => {
    const original = readCandidateEvidence(VALID_FULL);
    const dir = mkdtempSync(join(tmpdir(), "candidate-evidence-"));
    const out = join(dir, "nested", "candidate.json");
    writeCandidateEvidence(original, out);
    const reloaded = readCandidateEvidence(out);
    expect(reloaded).toEqual(original);
  });

  test("writes a trailing newline", () => {
    const evidence = readCandidateEvidence(VALID_FULL);
    const dir = mkdtempSync(join(tmpdir(), "candidate-evidence-"));
    const out = join(dir, "candidate.json");
    writeCandidateEvidence(evidence, out);
    expect(readFileSync(out, "utf8").endsWith("\n")).toBe(true);
  });

  test("readCandidateEvidence throws on an invalid fixture", () => {
    expect(() => readCandidateEvidence(INVALID_MISSING)).toThrow(/hypothesis/);
  });
});
