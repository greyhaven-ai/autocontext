import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, test, expect } from "vitest";
import {
  repairToolCallJson,
  repairArtifactLanding,
  finishGuard,
} from "../../src/harness-optimization/repairs.js";
import { validateRepairResult } from "../../src/harness-optimization/contract/validators.js";
import type { ArtifactContractProbeInputs } from "../../src/control-plane/contract-probes/index.js";

// Load the SAME repo-root fixture the Python suite loads. Matching it in both
// languages is the load-bearing parity proof: both implementations must return
// the same status (and target / reason substring) for every recorded input.
// Walk up to the repo root: ts/tests/harness-optimization/ -> ts/tests/ -> ts/ -> <repo root>.
const FIXTURE = join(
  import.meta.dirname,
  "..",
  "..",
  "..",
  "fixtures",
  "harness-optimization",
  "repair-cases",
  "repair-cases.json",
);

interface ExpectedDecision {
  status: "applied" | "skipped" | "not_applicable";
  reason_contains?: string;
  target?: string;
}

interface ToolCallCase {
  name: string;
  raw: string;
  expected: ExpectedDecision;
}

interface ArtifactContractFixture {
  path: string;
  content: string;
  expected_line_ending?: "lf" | "crlf";
  required_substrings?: string[];
  forbidden_substrings?: string[];
  required_json_fields?: string[];
}

interface ArtifactLandingCase {
  name: string;
  expected_contract: ArtifactContractFixture;
  produced: Record<string, string>;
  expected: ExpectedDecision;
}

interface FinishGuardCase {
  name: string;
  claimed_done: boolean;
  completion_ok: boolean;
  reason_if_not: string;
  expected: ExpectedDecision;
}

const cases = JSON.parse(readFileSync(FIXTURE, "utf8")) as {
  tool_call: ToolCallCase[];
  artifact_landing: ArtifactLandingCase[];
  finish_guard: FinishGuardCase[];
};

// Map the language-neutral snake_case contract in the fixture to the camelCase
// probe inputs the TypeScript repair consumes.
function toProbeInputs(c: ArtifactContractFixture): ArtifactContractProbeInputs {
  return {
    path: c.path,
    content: c.content,
    expectedLineEnding: c.expected_line_ending,
    requiredSubstrings: c.required_substrings,
    forbiddenSubstrings: c.forbidden_substrings,
    requiredJsonFields: c.required_json_fields,
  };
}

function assertDecision(
  actual: { status: string; reason: string; target?: string },
  expected: ExpectedDecision,
): void {
  expect(actual.status).toBe(expected.status);
  if (expected.reason_contains !== undefined) {
    expect(actual.reason).toContain(expected.reason_contains);
  }
  if (expected.target !== undefined) {
    expect(actual.target).toBe(expected.target);
  }
}

describe("repairToolCallJson matches the shared fixture", () => {
  for (const c of cases.tool_call) {
    test(c.name, () => {
      const { result } = repairToolCallJson(c.raw);
      assertDecision(result, c.expected);
      expect(validateRepairResult(result).valid).toBe(true);
    });
  }
});

describe("repairArtifactLanding matches the shared fixture", () => {
  for (const c of cases.artifact_landing) {
    test(c.name, () => {
      const { result } = repairArtifactLanding({
        expected: toProbeInputs(c.expected_contract),
        produced: c.produced,
      });
      assertDecision(result, c.expected);
      expect(validateRepairResult(result).valid).toBe(true);
    });
  }
});

describe("finishGuard matches the shared fixture", () => {
  for (const c of cases.finish_guard) {
    test(c.name, () => {
      const result = finishGuard({
        claimedDone: c.claimed_done,
        completionOk: c.completion_ok,
        reasonIfNot: c.reason_if_not,
      });
      assertDecision(result, c.expected);
      expect(validateRepairResult(result).valid).toBe(true);
    });
  }
});
