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

// AC-679 slice 3d: WeaknessReport variant. Mirrors Python's
// `WeaknessReport` shape (recommendation-focused, with recovery analysis)
// alongside the existing `TraceFindingReport` (writeup-style summary).

export const WeaknessReportSchema = z.object({
  reportId: z.string().min(1),
  traceId: z.string().min(1),
  sourceHarness: z.string().min(1),
  weaknesses: z.array(TraceFindingSchema),
  failureMotifs: z.array(FailureMotifSchema),
  recoveryAnalysis: z.string(),
  recommendations: z.array(z.string()),
  summary: z.string().min(1),
  createdAt: z.string().datetime({ message: "createdAt must be ISO 8601 format" }),
  metadata: z.record(z.unknown()).default({}),
});

export type WeaknessReport = z.infer<typeof WeaknessReportSchema>;

// Per-category recommendation copy. Deliberately a single line each so
// downstream Markdown / HTML rendering stays predictable; a future slice
// can expand to multi-line or context-aware suggestions.
const RECOMMENDATION_BY_CATEGORY: Record<TraceFindingCategory, string> = {
  tool_call_failure:
    "Add retry-on-error or argument validation around the failing tool to reduce repeat failures.",
  agent_refusal:
    "Review the prompt for ambiguous instructions or conflict with safety policy that may trigger refusals.",
  low_outcome_score:
    "Inspect the outcome reasoning field for specific failure points and revise the harness or task definition.",
  dimension_inconsistency:
    "Re-weight or recalibrate scoring dimensions so they reflect a coherent quality signal.",
};

function composeRecoveryAnalysis(trace: PublicTrace, weaknessCount: number): string {
  if (!trace.outcome) {
    return weaknessCount === 0
      ? "Trace completed without an explicit outcome; no weaknesses surfaced."
      : "Trace completed without an explicit outcome; weaknesses were recorded but recovery is undetermined.";
  }
  const score = trace.outcome.score;
  if (weaknessCount === 0) {
    return `Trace concluded cleanly with outcome score ${score.toFixed(2)}.`;
  }
  if (score >= LOW_SCORE_THRESHOLD) {
    return `Trace concluded with outcome score ${score.toFixed(2)} above the ${LOW_SCORE_THRESHOLD} threshold despite ${weaknessCount} weakness(es); some recovery occurred.`;
  }
  return `Trace concluded with outcome score ${score.toFixed(2)} below the ${LOW_SCORE_THRESHOLD} threshold; no recovery observed across ${weaknessCount} weakness(es).`;
}

export function generateWeaknessReport(
  trace: PublicTrace,
  options: GenerateTraceFindingReportOptions = {},
): WeaknessReport {
  const clock = options.now ?? ((): Date => new Date());
  const weaknesses = extractFindings(trace);
  const motifs = extractFailureMotifs(weaknesses);

  // One recommendation per distinct category surfaced (deduplicated).
  const categoriesSeen = new Set<TraceFindingCategory>();
  for (const weakness of weaknesses) {
    categoriesSeen.add(weakness.category);
  }
  const recommendations = [...categoriesSeen]
    .sort((left, right) => left.localeCompare(right))
    .map((category) => RECOMMENDATION_BY_CATEGORY[category]);

  const recoveryAnalysis = composeRecoveryAnalysis(trace, weaknesses.length);

  const summary =
    weaknesses.length === 0
      ? "No weaknesses identified."
      : `${weaknesses.length} weakness(es) detected across ${motifs.length} category(ies).`;
  const timestamp = clock().toISOString();
  return {
    reportId: `weakness-${trace.traceId}-${timestamp}`,
    traceId: trace.traceId,
    sourceHarness: trace.sourceHarness,
    weaknesses,
    failureMotifs: motifs,
    recoveryAnalysis,
    recommendations,
    summary,
    createdAt: timestamp,
    metadata: {},
  };
}

export function renderWeaknessReportMarkdown(report: WeaknessReport): string {
  const lines: string[] = [
    `# Weakness Report: ${report.traceId}`,
    `**Source:** ${report.sourceHarness}`,
    "",
    "## Summary",
    report.summary,
    "",
    "## Weaknesses",
  ];
  if (report.weaknesses.length === 0) {
    lines.push("No weaknesses identified.");
  } else {
    for (const weakness of report.weaknesses) {
      const evidence =
        weakness.evidenceMessageIndexes.length === 0
          ? "evidence: none"
          : `evidence: ${weakness.evidenceMessageIndexes.map((index) => `msg #${index}`).join(", ")}`;
      lines.push(
        `- **${weakness.title}** [${weakness.category}/${weakness.severity}] ${weakness.description} (${evidence})`,
      );
    }
  }

  lines.push("", "## Recovery Analysis", report.recoveryAnalysis, "");

  lines.push("## Recommendations");
  if (report.recommendations.length === 0) {
    lines.push("No actionable recommendations.");
  } else {
    for (const recommendation of report.recommendations) {
      lines.push(`- ${recommendation}`);
    }
  }
  return lines.join("\n");
}
