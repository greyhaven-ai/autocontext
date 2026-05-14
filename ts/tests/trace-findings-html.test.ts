/**
 * AC-679 (slice 3c): HTML rendering for TraceFindingReport.
 *
 * Mirrors the Python `render_trace_writeup_html` shape (sections,
 * data attributes for client filtering, escaped user content, anchored
 * evidence references) so the same operator workflow transfers.
 */

import { describe, expect, it } from "vitest";

import {
  generateTraceFindingReport,
  renderTraceFindingReportHtml,
  SCHEMA_VERSION,
  type PublicTrace,
} from "../src/index.js";

function trace(overrides: Partial<PublicTrace> = {}): PublicTrace {
  return {
    schemaVersion: SCHEMA_VERSION,
    traceId: "trace_html_1",
    sourceHarness: "autocontext",
    collectedAt: "2026-05-14T12:00:00Z",
    messages: [
      { role: "user", content: "<unsafe>", timestamp: "2026-05-14T12:00:01Z" },
      {
        role: "assistant",
        content: "Trying.",
        timestamp: "2026-05-14T12:00:02Z",
        toolCalls: [{ toolName: "patch", args: {}, error: "<bad> hunk failed" }],
      },
    ],
    outcome: { score: 0.2, reasoning: "<broken> & rejected", dimensions: {} },
    ...overrides,
  };
}

describe("renderTraceFindingReportHtml", () => {
  it("emits the expected document scaffolding", () => {
    const report = generateTraceFindingReport(trace());
    const html = renderTraceFindingReportHtml(report);

    expect(html).toMatch(/<!doctype html>/i);
    // Title carries the trace id so an operator scanning many artifacts
    // can identify the source without opening each one.
    expect(html).toContain(`<title>Trace Findings: ${report.traceId}</title>`);
    expect(html).toContain('<section class="findings"');
    expect(html).toContain('<section class="motifs"');
  });

  it("escapes < > & in user-originated content (description, title, summary)", () => {
    const report = generateTraceFindingReport(trace());
    const html = renderTraceFindingReportHtml(report);

    // No raw user-supplied angle brackets / ampersands should appear.
    expect(html).not.toContain("<bad> hunk failed");
    expect(html).not.toContain("<broken> & rejected");
    expect(html).toContain("&lt;bad&gt;");
    expect(html).toContain("&lt;broken&gt;");
    expect(html).toContain("&amp;");
    // Sanity: no stray <script> tag could survive from a malicious payload.
    expect(html).not.toMatch(/<script[^>]*>(?!\s*\/\*)/i);
  });

  it("anchors each finding so external references can link directly", () => {
    const report = generateTraceFindingReport(trace());
    const html = renderTraceFindingReportHtml(report);

    for (const finding of report.findings) {
      expect(html).toContain(`id="finding-${finding.findingId}"`);
    }
  });

  it("exposes data-category attributes for client-side filtering", () => {
    const report = generateTraceFindingReport(trace());
    const html = renderTraceFindingReportHtml(report);

    expect(html).toMatch(/data-category="tool_call_failure"/);
    expect(html).toMatch(/data-category="low_outcome_score"/);
    // Motifs carry the same attribute so a filter UI can hide entire
    // motif rows alongside the matching finding rows.
    expect(html).toMatch(/<li class="motif" data-category="tool_call_failure"/);
  });

  it("emits compact empty states when no findings or motifs are present", () => {
    const t = trace({
      outcome: { score: 0.99, reasoning: "OK", dimensions: {} },
      messages: [{ role: "user", content: "noop", timestamp: "2026-05-14T12:00:01Z" }],
    });
    const report = generateTraceFindingReport(t);
    const html = renderTraceFindingReportHtml(report);

    expect(html).toContain("No notable findings.");
    expect(html).toContain("No recurring failure motifs.");
  });

  it("includes a self-contained <style> block (offline-first)", () => {
    const report = generateTraceFindingReport(trace());
    const html = renderTraceFindingReportHtml(report);

    expect(html).toMatch(/<style[^>]*>[\s\S]*<\/style>/);
    // Hard pin: no external stylesheet links (we want offline-first).
    expect(html).not.toMatch(/<link[^>]*rel="stylesheet"[^>]*href="http/);
  });

  it("renders evidenceMessageIndexes as 'msg #N' references inside each finding", () => {
    const report = generateTraceFindingReport(trace());
    const html = renderTraceFindingReportHtml(report);

    expect(html).toMatch(/msg #1/);
  });
});
