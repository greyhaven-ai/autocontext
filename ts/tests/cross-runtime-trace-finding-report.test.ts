/**
 * AC-679 (slice 3a): cross-runtime TraceFindingReport JSON contract.
 *
 * The shared fixture at `fixtures/cross-runtime/trace-finding-report.json`
 * (at repo root) is the wire-format contract that both Python and TS
 * validate against. This file pins the TS side; the Python side has a
 * mirror at `autocontext/tests/test_cross_runtime_trace_findings.py`.
 *
 * If either runtime adds or renames a field in its schema without the
 * other following, one of the two parity tests breaks before a real-world
 * cross-runtime report can fail to parse.
 */

import { readFile } from "node:fs/promises";
import { resolve as pathResolve } from "node:path";

import { describe, expect, it } from "vitest";

import {
  TRACE_FINDING_CATEGORIES,
  TraceFindingReportSchema,
  type TraceFindingReport,
} from "../src/index.js";

const FIXTURE_PATH = pathResolve(
  import.meta.dirname,
  "..",
  "..",
  "fixtures",
  "cross-runtime",
  "trace-finding-report.json",
);

async function loadFixture(): Promise<unknown> {
  const raw = await readFile(FIXTURE_PATH, "utf8");
  return JSON.parse(raw);
}

describe("cross-runtime TraceFindingReport contract", () => {
  it("validates the shared fixture under TraceFindingReportSchema", async () => {
    const fixture = await loadFixture();
    const result = TraceFindingReportSchema.safeParse(fixture);
    expect(result.success).toBe(true);
    if (result.success) {
      const report: TraceFindingReport = result.data;
      expect(report.traceId).toBe("trace_cross_runtime_canonical");
      expect(report.sourceHarness).toBe("autocontext");
      expect(report.findings).toHaveLength(2);
      expect(report.failureMotifs).toHaveLength(2);
      expect(report.findings[0]?.findingId).toBe("finding-0");
      expect(report.findings[0]?.category).toBe("tool_call_failure");
      expect(report.findings[1]?.category).toBe("low_outcome_score");
    }
  });

  it("keeps TS taxonomy in lockstep with Python", async () => {
    // The Python module `cross_runtime_trace_findings.py` exports the same
    // tuple. A mismatch here AND in the Python parity test means a
    // taxonomy was added on one runtime without the other.
    expect([...TRACE_FINDING_CATEGORIES].sort()).toEqual([
      "agent_refusal",
      "dimension_inconsistency",
      "low_outcome_score",
      "tool_call_failure",
    ]);
  });

  it("rejects an unknown category in the shared fixture shape", async () => {
    const fixture = (await loadFixture()) as TraceFindingReport;
    const mutated = {
      ...fixture,
      findings: [{ ...fixture.findings[0], category: "not_a_real_category" }],
    };
    const result = TraceFindingReportSchema.safeParse(mutated);
    expect(result.success).toBe(false);
  });

  it("rejects a non-positive occurrenceCount in the shared fixture shape", async () => {
    const fixture = (await loadFixture()) as TraceFindingReport;
    const mutated = {
      ...fixture,
      failureMotifs: [{ ...fixture.failureMotifs[0], occurrenceCount: 0 }],
    };
    const result = TraceFindingReportSchema.safeParse(mutated);
    expect(result.success).toBe(false);
  });

  it("rejects a missing required field in the shared fixture shape", async () => {
    const fixture = (await loadFixture()) as Record<string, unknown>;
    const { traceId: _omitted, ...mutated } = fixture;
    const result = TraceFindingReportSchema.safeParse(mutated);
    expect(result.success).toBe(false);
  });

  it("rejects a negative evidenceMessageIndexes entry", async () => {
    const fixture = (await loadFixture()) as TraceFindingReport;
    const mutated = {
      ...fixture,
      findings: [
        { ...fixture.findings[0], evidenceMessageIndexes: [-1] },
        ...fixture.findings.slice(1),
      ],
    };
    const result = TraceFindingReportSchema.safeParse(mutated);
    expect(result.success).toBe(false);
  });
});
