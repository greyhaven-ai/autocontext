import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it, expect } from "vitest";
import { auditLeakage } from "../../src/harness-optimization/leakage.js";
import { evaluateLeakageGate } from "../../src/harness-optimization/leakage-gate.js";

// Load the SAME repo-root fixture the Python suite loads. Matching it in both
// languages is the load-bearing parity proof.
// Walk up to the repo root: ts/tests/harness-optimization/ -> ts/tests/ -> ts/ -> <repo root>.
const CASES = JSON.parse(
  readFileSync(
    join(
      import.meta.dirname,
      "..",
      "..",
      "..",
      "fixtures",
      "harness-optimization",
      "leakage-cases",
      "leakage-cases.json",
    ),
    "utf8",
  ),
).cases.filter((c: { gate?: unknown }) => c.gate !== undefined);

describe("leakage gate parity", () => {
  for (const c of CASES) {
    it(`${c.name}: advance + non_promotion_grade match fixture`, () => {
      const audit = auditLeakage(c.metadata, c.access_records);
      const decision = evaluateLeakageGate(
        audit,
        c.metadata.mode,
        c.metadata.prompt_provenance ?? "",
      );
      expect(decision.advance).toBe(c.gate.expected_advance);
      expect(decision.non_promotion_grade).toBe(c.gate.expected_non_promotion_grade);
    });
  }
});
