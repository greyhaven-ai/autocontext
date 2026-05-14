/**
 * AC-679 (slice 1): TS trace-finding report parity.
 *
 * Reaches for the cross-runtime AC-679 contract by extracting structured
 * `TraceFinding`s from `PublicTrace` (the TS data plane primitive) rather
 * than from a Python-shape RunTrace. Parity moves to the *output*
 * (TraceFindingReport JSON) instead of the input artifact, so each runtime
 * can extract from its own canonical trace shape.
 *
 * Slice 1 ships the schema + pure library only; CLI subcommand and HTML
 * rendering land in follow-up slices.
 */

import { describe, expect, it } from "vitest";

import {
  FailureMotifSchema,
  SCHEMA_VERSION,
  TRACE_FINDING_CATEGORIES,
  TraceFindingReportSchema,
  TraceFindingSchema,
  extractFailureMotifs,
  extractFindings,
  generateTraceFindingReport,
  renderTraceFindingReportMarkdown,
  type PublicTrace,
} from "../src/index.js";

function tracePart(overrides: Partial<PublicTrace> = {}): PublicTrace {
  return {
    schemaVersion: SCHEMA_VERSION,
    traceId: "trace_abc",
    sourceHarness: "autocontext",
    collectedAt: "2026-05-13T12:00:00Z",
    messages: [
      { role: "user", content: "Fix the login bug", timestamp: "2026-05-13T12:00:01Z" },
      {
        role: "assistant",
        content: "I'll investigate.",
        timestamp: "2026-05-13T12:00:02Z",
        toolCalls: [{ toolName: "read", args: { path: "auth.ts" } }],
      },
    ],
    ...overrides,
  };
}

describe("Zod schemas", () => {
  it("exposes a fixed taxonomy", () => {
    expect(TRACE_FINDING_CATEGORIES).toEqual(
      expect.arrayContaining([
        "tool_call_failure",
        "agent_refusal",
        "low_outcome_score",
        "dimension_inconsistency",
      ]),
    );
  });

  it("rejects findings with unknown categories", () => {
    const bad = TraceFindingSchema.safeParse({
      findingId: "f1",
      category: "not_a_category",
      severity: "low",
      title: "x",
      description: "y",
      evidenceMessageIndexes: [0],
    });
    expect(bad.success).toBe(false);
  });

  it("rejects motifs with non-positive occurrence counts", () => {
    const bad = FailureMotifSchema.safeParse({
      motifId: "m1",
      category: "tool_call_failure",
      occurrenceCount: 0,
      evidenceMessageIndexes: [],
      description: "x",
    });
    expect(bad.success).toBe(false);
  });

  it("round-trips a full report through TraceFindingReportSchema", () => {
    const report = {
      reportId: "report-1",
      traceId: "trace_abc",
      sourceHarness: "autocontext",
      findings: [],
      failureMotifs: [],
      summary: "Empty.",
      createdAt: "2026-05-13T12:00:00Z",
      metadata: {},
    };
    const parsed = TraceFindingReportSchema.parse(report);
    expect(parsed.traceId).toBe("trace_abc");
  });
});

describe("extractFindings", () => {
  it("flags toolCalls with non-empty error as tool_call_failure", () => {
    const trace = tracePart({
      messages: [
        { role: "user", content: "Edit foo.ts", timestamp: "2026-05-13T12:00:01Z" },
        {
          role: "assistant",
          content: "On it.",
          timestamp: "2026-05-13T12:00:02Z",
          toolCalls: [
            {
              toolName: "patch",
              args: { path: "foo.ts" },
              error: "patch hunk does not apply",
            },
          ],
        },
      ],
    });

    const findings = extractFindings(trace);

    expect(findings).toHaveLength(1);
    expect(findings[0]?.category).toBe("tool_call_failure");
    // Evidence must point back to the assistant message index so consumers
    // can navigate to the source of the failure.
    expect(findings[0]?.evidenceMessageIndexes).toEqual([1]);
  });

  it("flags refusal-pattern assistant content as agent_refusal", () => {
    const trace = tracePart({
      messages: [
        { role: "user", content: "Patch this", timestamp: "2026-05-13T12:00:01Z" },
        {
          role: "assistant",
          content: "I cannot make that change.",
          timestamp: "2026-05-13T12:00:02Z",
        },
      ],
    });

    const findings = extractFindings(trace);

    expect(findings.some((f) => f.category === "agent_refusal")).toBe(true);
  });

  it("flags low outcome score as low_outcome_score", () => {
    const trace = tracePart({
      outcome: { score: 0.3, reasoning: "Tests still failing.", dimensions: {} },
    });

    const findings = extractFindings(trace);

    expect(findings.some((f) => f.category === "low_outcome_score")).toBe(true);
  });

  it("does not flag healthy traces as low_outcome_score", () => {
    const trace = tracePart({
      outcome: { score: 0.95, reasoning: "All checks pass.", dimensions: {} },
    });

    const findings = extractFindings(trace);

    expect(findings.some((f) => f.category === "low_outcome_score")).toBe(false);
  });

  it("flags inconsistent outcome dimensions as dimension_inconsistency", () => {
    const trace = tracePart({
      outcome: {
        score: 0.7,
        reasoning: "Mixed signals.",
        dimensions: { correctness: 0.1, polish: 0.95 },
      },
    });

    const findings = extractFindings(trace);

    expect(findings.some((f) => f.category === "dimension_inconsistency")).toBe(true);
  });
});

describe("extractFailureMotifs", () => {
  it("groups findings by category with occurrence counts", () => {
    const trace = tracePart({
      messages: [
        { role: "user", content: "x", timestamp: "2026-05-13T12:00:01Z" },
        {
          role: "assistant",
          content: "Trying.",
          timestamp: "2026-05-13T12:00:02Z",
          toolCalls: [{ toolName: "patch", args: {}, error: "hunk failed" }],
        },
        {
          role: "assistant",
          content: "Retrying.",
          timestamp: "2026-05-13T12:00:03Z",
          toolCalls: [{ toolName: "patch", args: {}, error: "hunk failed again" }],
        },
      ],
    });

    const findings = extractFindings(trace);
    const motifs = extractFailureMotifs(findings);

    const toolMotif = motifs.find((m) => m.category === "tool_call_failure");
    expect(toolMotif).toBeDefined();
    expect(toolMotif?.occurrenceCount).toBe(2);
    expect(toolMotif?.evidenceMessageIndexes).toEqual([1, 2]);
  });

  it("produces no motifs when there are no findings", () => {
    expect(extractFailureMotifs([])).toEqual([]);
  });
});

describe("generateTraceFindingReport", () => {
  it("composes a deterministic report with stable ids when given a clock", () => {
    const trace = tracePart({
      outcome: { score: 0.2, reasoning: "Broken.", dimensions: {} },
    });

    const now = () => new Date("2026-05-13T13:00:00Z");
    const report = generateTraceFindingReport(trace, { now });

    expect(report.traceId).toBe(trace.traceId);
    expect(report.sourceHarness).toBe(trace.sourceHarness);
    expect(report.createdAt).toBe("2026-05-13T13:00:00.000Z");
    expect(report.findings.length).toBeGreaterThan(0);
    expect(report.failureMotifs.length).toBeGreaterThan(0);
    expect(report.summary).toMatch(/finding/i);
  });

  it("validates against TraceFindingReportSchema", () => {
    const trace = tracePart({
      outcome: { score: 0.4, reasoning: "Eh.", dimensions: {} },
    });
    const report = generateTraceFindingReport(trace);
    expect(TraceFindingReportSchema.safeParse(report).success).toBe(true);
  });
});

describe("renderTraceFindingReportMarkdown", () => {
  it("emits the expected sections + evidence references", () => {
    const trace = tracePart({
      outcome: { score: 0.2, reasoning: "Broken.", dimensions: {} },
      messages: [
        { role: "user", content: "x", timestamp: "2026-05-13T12:00:01Z" },
        {
          role: "assistant",
          content: "Trying.",
          timestamp: "2026-05-13T12:00:02Z",
          toolCalls: [{ toolName: "patch", args: {}, error: "hunk failed" }],
        },
      ],
    });
    const report = generateTraceFindingReport(trace);
    const md = renderTraceFindingReportMarkdown(report);

    expect(md).toContain(`# Trace Findings: ${trace.traceId}`);
    expect(md).toContain("## Findings");
    expect(md).toContain("## Failure Motifs");
    // Evidence message indexes must round-trip into the rendered Markdown
    // so operators can correlate findings with the source transcript.
    expect(md).toMatch(/evidence:.*msg #1/);
  });

  it("emits compact empty states when nothing is found", () => {
    const trace = tracePart({
      outcome: { score: 0.99, reasoning: "All good.", dimensions: {} },
    });
    const report = generateTraceFindingReport(trace);
    const md = renderTraceFindingReportMarkdown(report);

    expect(md).toContain("No notable findings.");
    expect(md).toContain("No recurring failure motifs.");
  });
});
