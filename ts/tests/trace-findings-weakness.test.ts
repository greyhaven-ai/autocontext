/**
 * AC-679 (slice 3d): WeaknessReport variant.
 *
 * The Python side ships TWO report flavors out of `TraceReporter`:
 * `TraceWriteup` (positive summary, motifs, recoveries) and `WeaknessReport`
 * (recommendation-focused, with recovery analysis text). Slice 1 shipped
 * the TS analog of the writeup; slice 3d adds the weakness variant so
 * `autoctx trace-findings --kind weakness` (next CLI slice) and downstream
 * tooling can route to either output flavor.
 */

import { describe, expect, it } from "vitest";

import {
  generateWeaknessReport,
  renderWeaknessReportMarkdown,
  SCHEMA_VERSION,
  WeaknessReportSchema,
  type PublicTrace,
} from "../src/index.js";

function trace(overrides: Partial<PublicTrace> = {}): PublicTrace {
  return {
    schemaVersion: SCHEMA_VERSION,
    traceId: "trace_weakness_1",
    sourceHarness: "autocontext",
    collectedAt: "2026-05-14T12:00:00Z",
    messages: [
      { role: "user", content: "Patch foo.ts", timestamp: "2026-05-14T12:00:01Z" },
      {
        role: "assistant",
        content: "Trying.",
        timestamp: "2026-05-14T12:00:02Z",
        toolCalls: [{ toolName: "patch", args: {}, error: "hunk failed" }],
      },
    ],
    outcome: { score: 0.2, reasoning: "Broken.", dimensions: {} },
    ...overrides,
  };
}

describe("WeaknessReportSchema", () => {
  it("requires recoveryAnalysis and recommendations alongside weaknesses", () => {
    const bad = WeaknessReportSchema.safeParse({
      reportId: "r1",
      traceId: "t1",
      sourceHarness: "x",
      weaknesses: [],
      failureMotifs: [],
      summary: "x",
      createdAt: "2026-05-14T12:00:00.000Z",
    });
    expect(bad.success).toBe(false);
  });

  it("accepts a structurally complete weakness report", () => {
    const ok = WeaknessReportSchema.safeParse({
      reportId: "r1",
      traceId: "t1",
      sourceHarness: "x",
      weaknesses: [],
      failureMotifs: [],
      recoveryAnalysis: "n/a",
      recommendations: [],
      summary: "x",
      createdAt: "2026-05-14T12:00:00.000Z",
      metadata: {},
    });
    expect(ok.success).toBe(true);
  });
});

describe("generateWeaknessReport", () => {
  it("emits weaknesses + recommendations + recovery analysis from a PublicTrace", () => {
    const report = generateWeaknessReport(trace(), {
      now: () => new Date("2026-05-14T13:00:00Z"),
    });

    expect(report.traceId).toBe("trace_weakness_1");
    expect(report.weaknesses.length).toBeGreaterThan(0);
    // Recommendations are per-category boilerplate at slice 3d; they
    // exist (non-empty list) when at least one weakness fired.
    expect(report.recommendations.length).toBeGreaterThan(0);
    // Recovery analysis is a non-empty string narrative; for a 0.2-score
    // outcome with findings, it should mention "no recovery" or similar.
    expect(report.recoveryAnalysis.length).toBeGreaterThan(0);
    expect(report.recoveryAnalysis.toLowerCase()).toMatch(/no recovery|below|threshold/);
    expect(report.createdAt).toBe("2026-05-14T13:00:00.000Z");
  });

  it("produces a 'no weaknesses' report when nothing fires", () => {
    const t = trace({
      outcome: { score: 0.95, reasoning: "All good.", dimensions: {} },
      messages: [{ role: "user", content: "noop", timestamp: "2026-05-14T12:00:01Z" }],
    });
    const report = generateWeaknessReport(t);

    expect(report.weaknesses).toHaveLength(0);
    expect(report.failureMotifs).toHaveLength(0);
    // Recommendations may be empty when no weaknesses fire; that's fine.
    expect(Array.isArray(report.recommendations)).toBe(true);
    // Recovery analysis still gets a value (the absence statement is itself
    // informative); we just pin it's a non-empty string.
    expect(report.recoveryAnalysis.length).toBeGreaterThan(0);
  });

  it("recommendations are per-category and deduplicated", () => {
    const t = trace({
      messages: [
        { role: "user", content: "x", timestamp: "2026-05-14T12:00:01Z" },
        {
          role: "assistant",
          content: "Trying.",
          timestamp: "2026-05-14T12:00:02Z",
          toolCalls: [
            { toolName: "patch", args: {}, error: "first" },
            { toolName: "patch", args: {}, error: "second" },
          ],
        },
      ],
    });
    const report = generateWeaknessReport(t);

    // Two tool_call_failure findings should produce ONE recommendation
    // (not two duplicates).
    const toolRecs = report.recommendations.filter((r) => r.toLowerCase().includes("tool"));
    expect(toolRecs.length).toBe(1);
  });

  it("validates against WeaknessReportSchema", () => {
    const report = generateWeaknessReport(trace());
    expect(WeaknessReportSchema.safeParse(report).success).toBe(true);
  });
});

describe("renderWeaknessReportMarkdown", () => {
  it("emits the expected sections", () => {
    const report = generateWeaknessReport(trace());
    const md = renderWeaknessReportMarkdown(report);

    expect(md).toContain(`# Weakness Report: ${report.traceId}`);
    expect(md).toContain("## Weaknesses");
    expect(md).toContain("## Recovery Analysis");
    expect(md).toContain("## Recommendations");
  });

  it("emits compact empty states when no weaknesses fire", () => {
    const t = trace({
      outcome: { score: 0.95, reasoning: "All good.", dimensions: {} },
      messages: [{ role: "user", content: "noop", timestamp: "2026-05-14T12:00:01Z" }],
    });
    const report = generateWeaknessReport(t);
    const md = renderWeaknessReportMarkdown(report);

    expect(md).toContain("No weaknesses identified.");
    expect(md.toLowerCase()).toMatch(/no .* recommendations/);
  });
});
