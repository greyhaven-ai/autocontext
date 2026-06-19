import { createHash } from "node:crypto";

import type { AttributionResult, GenerationChangeVector } from "./credit-assignment-models.js";

export interface KnowledgeSpan {
  spanId: string;
  source: string;
  text: string;
  metadata: Record<string, unknown>;
}

export interface SpanCreditRow extends KnowledgeSpan {
  credit: number;
  evidenceLevel: "component_correlated";
  demoted?: boolean;
}

export interface SpanAttributionReport {
  schemaVersion: 1;
  mode: "span";
  spans: SpanCreditRow[];
}

function normalizeSpanText(text: string): string {
  return text.trim().replace(/^(?:[-*]\s+|\d+\.\s+)/, "").trim().replace(/\s+/g, " ");
}

function stableSpanId(source: string, text: string): string {
  const digest = createHash("sha1")
    .update(`${source}:${normalizeSpanText(text).toLowerCase()}`)
    .digest("hex")
    .slice(0, 12);
  return `${source}:${digest}`;
}

export function extractKnowledgeSpans(source: string, content: string): KnowledgeSpan[] {
  const spans: KnowledgeSpan[] = [];
  let ordinal = 0;
  content.split(/\r?\n/).forEach((line, index) => {
    const text = normalizeSpanText(line);
    if (!text) return;
    ordinal += 1;
    spans.push({
      spanId: stableSpanId(source, text),
      source,
      text,
      metadata: { source, lineNumber: index + 1, ordinal },
    });
  });
  return spans;
}

export function buildSpanAttribution(
  vector: GenerationChangeVector,
  attribution: AttributionResult,
  currentState: Record<string, unknown>,
): SpanAttributionReport {
  const rows: SpanCreditRow[] = [];
  for (const change of vector.changes) {
    const spans = extractKnowledgeSpans(change.component, String(currentState[change.component] ?? ""));
    if (spans.length === 0) continue;
    let componentCredit = attribution.credits[change.component] ?? 0;
    if (componentCredit === 0 && vector.scoreDelta < 0 && vector.totalChangeMagnitude > 0) {
      componentCredit = round(vector.scoreDelta * (change.magnitude / vector.totalChangeMagnitude));
    }
    const credit = round(componentCredit / spans.length);
    rows.push(...spans.map((span) => ({ ...span, credit, evidenceLevel: "component_correlated" as const })));
  }
  return { schemaVersion: 1, mode: "span", spans: rows };
}

export function rankSpansByCredit(
  spans: KnowledgeSpan[],
  credits: Record<string, number>,
): SpanCreditRow[] {
  return spans
    .map((span) => {
      const credit = credits[span.spanId] ?? 0;
      return { ...span, credit, evidenceLevel: "component_correlated" as const, demoted: credit <= 0 };
    })
    .sort((left, right) => right.credit - left.credit || left.spanId.localeCompare(right.spanId));
}

function round(value: number): number {
  return Math.round(value * 1_000_000) / 1_000_000;
}
