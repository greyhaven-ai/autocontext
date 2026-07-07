import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, test, expect } from "vitest";
import { validateRepairResult } from "../../src/harness-optimization/contract/validators.js";
import type { RepairResult } from "../../src/harness-optimization/contract/generated-types.js";

// Walk up to the repo root: ts/tests/harness-optimization/ -> ts/tests/ -> ts/ -> <repo root>.
const FIXTURES_DIR = join(
  import.meta.dirname,
  "..",
  "..",
  "..",
  "fixtures",
  "harness-optimization",
  "repair-result",
);

// The contract fixtures validated by both packages.
const EXPECTED_FIXTURES = new Set([
  "valid-applied.json",
  "valid-skipped.json",
  "invalid-missing-status.json",
  "invalid-bad-status.json",
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

// A minimal fully-valid RepairResult used as the fixture-of-record.
const validResult: RepairResult = {
  schema_version: 1,
  repair_name: "tool_call_json",
  status: "applied",
  reason: "The tool call arguments were not valid JSON; re-serialized before dispatch.",
  target: "run_python",
  before: { valid: false },
  after: { valid: true },
  parity: {
    python: "implemented",
    typescript: "implemented",
    schema_hash: "abc123",
  },
};

describe("validateRepairResult", () => {
  test("a fully-valid repair result validates", () => {
    const result = validateRepairResult(validResult);
    expect(result.valid).toBe(true);
  });

  test("a result missing the required status field fails", () => {
    const { status: _omitted, ...missingStatus } = validResult;
    const result = validateRepairResult(missingStatus);
    expect(result.valid).toBe(false);
    if (!result.valid) {
      expect(result.errors.join("\n")).toContain("status");
    }
  });

  test("a bad status enum value fails", () => {
    const result = validateRepairResult({ ...validResult, status: "bogus" });
    expect(result.valid).toBe(false);
  });

  test("a result with an unknown extra field fails (additionalProperties)", () => {
    const withExtra = { ...validResult, surprise_field: "nope" };
    const result = validateRepairResult(withExtra);
    expect(result.valid).toBe(false);
  });

  test("an empty parity object fails (python/typescript/schema_hash required)", () => {
    const emptyParity = { ...validResult, parity: {} };
    const result = validateRepairResult(emptyParity);
    expect(result.valid).toBe(false);
  });
});

describe("shared repo-root repair-result fixtures", () => {
  test.each(fixtures("valid-"))("%s validates", (name) => {
    const result = validateRepairResult(loadFixture(name));
    expect(result.valid).toBe(true);
  });

  test.each(fixtures("invalid-"))("%s fails validation", (name) => {
    const result = validateRepairResult(loadFixture(name));
    expect(result.valid).toBe(false);
  });

  test("the fixtures directory contains exactly the expected file set", () => {
    // Set-membership guard over EVERY .json in the directory: a stray or dropped
    // .json fails here instead of being silently ignored.
    const allJson = new Set(readdirSync(FIXTURES_DIR).filter((name) => name.endsWith(".json")));
    expect(allJson).toEqual(EXPECTED_FIXTURES);
  });
});
