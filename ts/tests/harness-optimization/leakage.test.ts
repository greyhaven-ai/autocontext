import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it, expect } from "vitest";
import { auditLeakage, renderLeakageReport } from "../../src/harness-optimization/leakage.js";
import { validateIntegrityMetadata } from "../../src/harness-optimization/contract/validators.js";

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
).cases;

describe("leakage audit parity", () => {
  for (const c of CASES) {
    it(`${c.name}: status + reason count match fixture`, () => {
      // Every fixture metadata must validate against the schema so a future
      // null/shape drift fails a test here.
      expect(validateIntegrityMetadata(c.metadata)).toEqual({ valid: true });
      const audit = auditLeakage(c.metadata, c.access_records);
      expect(audit.status).toBe(c.expected_status);
      expect(audit.reasons.length).toBe(c.expected_reason_count);
    });
  }
  it("renders allowed/forbidden/required/status in the report", () => {
    const c = CASES[0];
    const audit = auditLeakage(c.metadata, c.access_records);
    const report = renderLeakageReport(c.metadata, audit);
    expect(report).toContain(`status: ${audit.status}`);
    expect(report).toContain("forbidden_sources:");
    expect(report).toContain("required_sources:");
    expect(report).toContain("web_allowlist:");
  });
});
