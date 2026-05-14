/**
 * AC-679 (slice 1): trace-finding extraction over PublicTrace.
 *
 * Reaches for cross-runtime parity with Python's TraceReporter, but on the
 * *output* shape (TraceFindingReport JSON) rather than the input. Python
 * extracts findings from its harness-internal RunTrace; TS extracts them
 * from PublicTrace, the data plane artifact that actually flows through
 * `autoctx production-traces`. The two pipelines converge on the same
 * `TraceFindingReport` schema so downstream consumers can treat the report
 * as the cross-runtime contract.
 *
 * The taxonomy below intentionally targets agent-behavior failures detectable
 * from a PublicTrace (transcript + outcome + tool calls), which is exactly
 * the axis AC-678 explicitly deferred when it shipped the Python CLI on top
 * of harness-event-typed findings.
 */

import { z } from "zod";

import type { PublicTrace } from "../traces/public-schema-contracts.js";

export const TRACE_FINDING_CATEGORIES = [
  "tool_call_failure",
  "agent_refusal",
  "low_outcome_score",
  "dimension_inconsistency",
] as const;

export type TraceFindingCategory = (typeof TRACE_FINDING_CATEGORIES)[number];

export const TraceFindingCategorySchema = z.enum(TRACE_FINDING_CATEGORIES);

export const TraceFindingSchema = z.object({
  findingId: z.string().min(1),
  category: TraceFindingCategorySchema,
  severity: z.enum(["low", "medium", "high"]),
  title: z.string().min(1),
  description: z.string().min(1),
  evidenceMessageIndexes: z.array(z.number().int().nonnegative()),
});

export type TraceFinding = z.infer<typeof TraceFindingSchema>;

export const FailureMotifSchema = z.object({
  motifId: z.string().min(1),
  category: TraceFindingCategorySchema,
  occurrenceCount: z.number().int().positive(),
  evidenceMessageIndexes: z.array(z.number().int().nonnegative()),
  description: z.string().min(1),
});

export type FailureMotif = z.infer<typeof FailureMotifSchema>;

export const TraceFindingReportSchema = z.object({
  reportId: z.string().min(1),
  traceId: z.string().min(1),
  sourceHarness: z.string().min(1),
  findings: z.array(TraceFindingSchema),
  failureMotifs: z.array(FailureMotifSchema),
  summary: z.string().min(1),
  createdAt: z.string().datetime({ message: "createdAt must be ISO 8601 format" }),
  metadata: z.record(z.unknown()).default({}),
});

export type TraceFindingReport = z.infer<typeof TraceFindingReportSchema>;

// Heuristic constants for the slice-1 taxonomy. These are intentionally
// coarse; refinements (precise regex sets, error-message classification,
// etc.) can ship in follow-up slices without changing the report contract.
const REFUSAL_PATTERN = /^\s*I(?:'|\s+a)?\s*(cannot|can\s*not|can't|won't|am not able)/i;
const LOW_SCORE_THRESHOLD = 0.5;
const DIMENSION_INCONSISTENCY_SPREAD = 0.5;

export function extractFindings(trace: PublicTrace): TraceFinding[] {
  const findings: TraceFinding[] = [];
  let counter = 0;
  const id = (): string => `finding-${counter++}`;

  trace.messages.forEach((message, index) => {
    for (const call of message.toolCalls ?? []) {
      if (typeof call.error === "string" && call.error.trim().length > 0) {
        findings.push({
          findingId: id(),
          category: "tool_call_failure",
          severity: "high",
          title: `Tool call to '${call.toolName}' failed`,
          description: call.error,
          evidenceMessageIndexes: [index],
        });
      }
    }
    if (message.role === "assistant" && REFUSAL_PATTERN.test(message.content)) {
      const firstLine = message.content.split("\n")[0]?.slice(0, 200) ?? "";
      findings.push({
        findingId: id(),
        category: "agent_refusal",
        severity: "medium",
        title: "Agent refused to proceed",
        description: firstLine.length > 0 ? firstLine : "Refusal phrase detected.",
        evidenceMessageIndexes: [index],
      });
    }
  });

  if (trace.outcome) {
    if (trace.outcome.score < LOW_SCORE_THRESHOLD) {
      findings.push({
        findingId: id(),
        category: "low_outcome_score",
        severity: "high",
        title: `Outcome score ${trace.outcome.score.toFixed(2)} below ${LOW_SCORE_THRESHOLD}`,
        description: trace.outcome.reasoning,
        evidenceMessageIndexes: [],
      });
    }
    const dimensionValues = Object.values(trace.outcome.dimensions ?? {});
    if (dimensionValues.length >= 2) {
      const max = Math.max(...dimensionValues);
      const min = Math.min(...dimensionValues);
      if (max - min >= DIMENSION_INCONSISTENCY_SPREAD) {
        findings.push({
          findingId: id(),
          category: "dimension_inconsistency",
          severity: "medium",
          title: `Outcome dimensions diverge by ${(max - min).toFixed(2)}`,
          description: `dimensions: ${JSON.stringify(trace.outcome.dimensions)}`,
          evidenceMessageIndexes: [],
        });
      }
    }
  }

  return findings;
}

export function extractFailureMotifs(findings: readonly TraceFinding[]): FailureMotif[] {
  if (findings.length === 0) {
    return [];
  }
  const byCategory = new Map<TraceFindingCategory, TraceFinding[]>();
  for (const finding of findings) {
    const list = byCategory.get(finding.category) ?? [];
    list.push(finding);
    byCategory.set(finding.category, list);
  }

  const motifs: FailureMotif[] = [];
  let counter = 0;
  const sortedEntries = [...byCategory.entries()].sort(([left], [right]) =>
    left.localeCompare(right),
  );
  for (const [category, group] of sortedEntries) {
    const evidence = [...new Set(group.flatMap((finding) => finding.evidenceMessageIndexes))].sort(
      (a, b) => a - b,
    );
    motifs.push({
      motifId: `motif-${counter++}`,
      category,
      occurrenceCount: group.length,
      evidenceMessageIndexes: evidence,
      description: `${category} occurred ${group.length} time(s)`,
    });
  }
  return motifs;
}

export interface GenerateTraceFindingReportOptions {
  now?: () => Date;
}

export function generateTraceFindingReport(
  trace: PublicTrace,
  options: GenerateTraceFindingReportOptions = {},
): TraceFindingReport {
  const clock = options.now ?? ((): Date => new Date());
  const findings = extractFindings(trace);
  const motifs = extractFailureMotifs(findings);
  const summary =
    findings.length === 0
      ? "No notable findings."
      : `${findings.length} finding(s) across ${motifs.length} category(ies).`;
  const timestamp = clock().toISOString();
  return {
    reportId: `report-${trace.traceId}-${timestamp}`,
    traceId: trace.traceId,
    sourceHarness: trace.sourceHarness,
    findings,
    failureMotifs: motifs,
    summary,
    createdAt: timestamp,
    metadata: {},
  };
}

export function renderTraceFindingReportMarkdown(report: TraceFindingReport): string {
  const lines: string[] = [
    `# Trace Findings: ${report.traceId}`,
    `**Source:** ${report.sourceHarness}`,
    "",
    "## Summary",
    report.summary,
    "",
    "## Findings",
  ];
  if (report.findings.length === 0) {
    lines.push("No notable findings.");
  } else {
    for (const finding of report.findings) {
      const evidence =
        finding.evidenceMessageIndexes.length === 0
          ? "evidence: none"
          : `evidence: ${finding.evidenceMessageIndexes.map((index) => `msg #${index}`).join(", ")}`;
      lines.push(
        `- **${finding.title}** [${finding.category}/${finding.severity}] ${finding.description} (${evidence})`,
      );
    }
  }
  lines.push("", "## Failure Motifs");
  if (report.failureMotifs.length === 0) {
    lines.push("No recurring failure motifs.");
  } else {
    for (const motif of report.failureMotifs) {
      lines.push(`- **${motif.category}**: ${motif.occurrenceCount} occurrence(s)`);
    }
  }
  return lines.join("\n");
}

// AC-679 slice 3c: HTML rendering. Mirrors the shape of Python's
// `render_trace_writeup_html` (escaped user content, anchored evidence,
// data attributes for client-side filtering, offline-first <style> block).

const HTML_ENTITIES: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

function htmlEscape(value: string): string {
  return value.replace(/[&<>"']/g, (char) => HTML_ENTITIES[char] ?? char);
}

function evidenceHtml(indexes: readonly number[]): string {
  if (indexes.length === 0) return '<span class="evidence-none">evidence: none</span>';
  const items = indexes
    .map((index) => `<a class="evidence-ref" href="#msg-${index}">msg #${index}</a>`)
    .join(", ");
  return `<span class="evidence">evidence: ${items}</span>`;
}

const TRACE_FINDINGS_STYLE = `
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; max-width: 880px; margin: 2rem auto; padding: 0 1rem; color: #1f2933; }
  h1 { font-size: 1.5rem; border-bottom: 1px solid #d1d5db; padding-bottom: 0.25rem; }
  section { margin-top: 1.25rem; }
  .meta { color: #4b5563; font-size: 0.9rem; }
  ul { list-style: none; padding-left: 0; }
  li.finding, li.motif { padding: 0.5rem 0.75rem; border-left: 3px solid #d1d5db; margin: 0.25rem 0; }
  li.finding[data-severity="high"] { border-left-color: #b91c1c; }
  li.finding[data-severity="medium"] { border-left-color: #d97706; }
  li.finding[data-severity="low"] { border-left-color: #2563eb; }
  .tag { display: inline-block; font-size: 0.75rem; padding: 0.1rem 0.4rem; border-radius: 0.25rem; background: #e5e7eb; color: #1f2933; margin-right: 0.25rem; }
  .evidence-ref { color: #2563eb; text-decoration: none; }
  .evidence-ref:hover { text-decoration: underline; }
  .empty { color: #6b7280; font-style: italic; }
`.trim();

export function renderTraceFindingReportHtml(report: TraceFindingReport): string {
  const findings =
    report.findings.length === 0
      ? '<p class="empty">No notable findings.</p>'
      : `<ul>${report.findings
          .map((finding) => {
            return [
              `<li class="finding"`,
              ` id="finding-${htmlEscape(finding.findingId)}"`,
              ` data-category="${htmlEscape(finding.category)}"`,
              ` data-severity="${htmlEscape(finding.severity)}"`,
              `>`,
              `<strong>${htmlEscape(finding.title)}</strong>`,
              ` <span class="tag">${htmlEscape(finding.category)}</span>`,
              `<span class="tag">${htmlEscape(finding.severity)}</span>`,
              ` <span class="description">${htmlEscape(finding.description)}</span>`,
              ` ${evidenceHtml(finding.evidenceMessageIndexes)}`,
              `</li>`,
            ].join("");
          })
          .join("")}</ul>`;

  const motifs =
    report.failureMotifs.length === 0
      ? '<p class="empty">No recurring failure motifs.</p>'
      : `<ul>${report.failureMotifs
          .map((motif) => {
            return [
              `<li class="motif" data-category="${htmlEscape(motif.category)}">`,
              `<strong>${htmlEscape(motif.category)}</strong>: `,
              `${motif.occurrenceCount} occurrence(s)`,
              `</li>`,
            ].join("");
          })
          .join("")}</ul>`;

  return [
    "<!doctype html>",
    '<html lang="en">',
    "<head>",
    '<meta charset="utf-8">',
    `<title>Trace Findings: ${htmlEscape(report.traceId)}</title>`,
    `<style>${TRACE_FINDINGS_STYLE}</style>`,
    "</head>",
    "<body>",
    `<h1>Trace Findings: ${htmlEscape(report.traceId)}</h1>`,
    `<p class="meta">Source: ${htmlEscape(report.sourceHarness)} | Created: ${htmlEscape(report.createdAt)}</p>`,
    '<section class="summary">',
    "<h2>Summary</h2>",
    `<p>${htmlEscape(report.summary)}</p>`,
    "</section>",
    '<section class="findings">',
    "<h2>Findings</h2>",
    findings,
    "</section>",
    '<section class="motifs">',
    "<h2>Failure Motifs</h2>",
    motifs,
    "</section>",
    "</body>",
    "</html>",
  ].join("\n");
}
