import { randomBytes } from "node:crypto";

import { estimateTokens } from "../prompts/context-budget.js";
import type { CompactionEntry } from "./compaction-ledger.js";

interface ComponentTokenLimits {
  [key: string]: number | undefined;
}

export interface PromptCompactionOptions {
  context?: Record<string, unknown>;
  parentId?: string;
  idFactory?: () => string;
  timestampFactory?: () => string;
}

export interface PromptCompactionResult {
  components: Record<string, string>;
  entries: CompactionEntry[];
}

const DEFAULT_COMPONENT_TOKEN_LIMITS: ComponentTokenLimits = {
  playbook: 2800,
  lessons: 1600,
  analysis: 1800,
  trajectory: 1200,
  experiment_log: 1800,
  session_reports: 1400,
  research_protocol: 1200,
  evidence_manifest: 1200,
  evidence_manifest_analyst: 1200,
  evidence_manifest_architect: 1200,
  agent_task_playbook: 600,
  agent_task_best_output: 900,
  policy_refinement_rules: 1600,
  policy_refinement_interface: 1000,
  policy_refinement_criteria: 1000,
  policy_refinement_feedback: 1400,
  consultation_context: 400,
  consultation_strategy: 400,
};

const HISTORY_COMPONENTS = new Set<string>([
  "experiment_log",
  "session_reports",
  "policy_refinement_feedback",
]);

const TAIL_PRESERVING_COMPONENTS = new Set<string>([
  "agent_task_best_output",
  "consultation_context",
  "consultation_strategy",
]);

const IMPORTANT_KEYWORDS = [
  "root cause",
  "finding",
  "findings",
  "recommendation",
  "recommendations",
  "rollback",
  "guard",
  "freshness",
  "objective",
  "score",
  "hypothesis",
  "diagnosis",
  "regression",
  "failure",
  "mitigation",
];

export function compactPromptComponents(components: Record<string, string>): Record<string, string> {
  const result: Record<string, string> = {};
  for (const [key, value] of Object.entries(components)) {
    result[key] = compactPromptComponent(key, value);
  }
  return result;
}

export function compactPromptComponentsWithEntries(
  components: Record<string, string>,
  opts: PromptCompactionOptions = {},
): PromptCompactionResult {
  const compacted = compactPromptComponents(components);
  return {
    components: compacted,
    entries: compactionEntriesForComponents(components, compacted, opts),
  };
}

export function compactionEntriesForComponents(
  originalComponents: Record<string, string>,
  compactedComponents: Record<string, string>,
  opts: PromptCompactionOptions = {},
): CompactionEntry[] {
  const entries: CompactionEntry[] = [];
  let currentParentId = opts.parentId ?? "";
  const nextId = opts.idFactory ?? newEntryId;
  const nextTimestamp = opts.timestampFactory ?? utcTimestamp;

  for (const [key, value] of Object.entries(originalComponents)) {
    const compacted = compactedComponents[key] ?? value;
    if (!value || compacted === value) {
      continue;
    }
    const entryId = nextId();
    const entry = buildCompactionEntry({
      key,
      original: value,
      compacted,
      entryId,
      parentId: currentParentId,
      timestamp: nextTimestamp(),
      context: opts.context ?? {},
    });
    entries.push(entry);
    currentParentId = entryId;
  }

  return entries;
}

export function compactPromptComponent(key: string, value: string): string {
  if (!value) return value;
  const limit = DEFAULT_COMPONENT_TOKEN_LIMITS[key];
  if (limit === undefined) return value;
  return compactComponent(key, value, limit);
}

export function extractPromotableLines(text: string, maxItems = 3): string[] {
  if (!text.trim()) return [];

  const lines = text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const candidates: string[] = [];
  const seen = new Set<string>();
  const prioritizedLines: string[] = [];
  const fallbackLines: string[] = [];

  for (const line of lines) {
    const normalized = line.toLowerCase();
    const cleaned = line
      .replace(/\s+/g, " ")
      .trim()
      .replace(/^#+/, "")
      .trim()
      .replace(/^[-*]\s*/, "")
      .trim();
    const cleanedKey = cleaned.toLowerCase();
    if (!cleaned || seen.has(cleanedKey)) {
      continue;
    }
    if (line.startsWith("#")) {
      if (
        cleanedKey !== "findings"
        && cleanedKey !== "summary"
        && !cleanedKey.startsWith("session report")
      ) {
        fallbackLines.push(cleaned);
      }
    } else if (
      line.startsWith("- ")
      || line.startsWith("* ")
      || IMPORTANT_KEYWORDS.some((keyword) => normalized.includes(keyword))
    ) {
      prioritizedLines.push(cleaned);
    }
  }

  for (const cleaned of [...prioritizedLines, ...fallbackLines]) {
    const key = cleaned.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    candidates.push(cleaned.slice(0, 220));
    if (candidates.length >= maxItems) break;
  }

  if (candidates.length > 0) return candidates;
  const fallback = text.replace(/\s+/g, " ").trim();
  return fallback ? [fallback.slice(0, 220)] : [];
}

interface BuildCompactionEntryInput {
  key: string;
  original: string;
  compacted: string;
  entryId: string;
  parentId: string;
  timestamp: string;
  context: Record<string, unknown>;
}

function buildCompactionEntry(input: BuildCompactionEntryInput): CompactionEntry {
  const tokensBefore = estimateTokens(input.original);
  const tokensAfter = estimateTokens(input.compacted);
  const details: Record<string, unknown> = {
    component: input.key,
    source: "prompt_components",
    tokensAfter,
    contentLengthBefore: input.original.length,
    contentLengthAfter: input.compacted.length,
    ...input.context,
  };

  return {
    type: "compaction",
    id: input.entryId,
    parentId: input.parentId,
    timestamp: input.timestamp,
    summary: structuredCompactionSummary(input.key, tokensBefore, tokensAfter, input.compacted),
    firstKeptEntryId: `component:${input.key}:kept`,
    tokensBefore,
    details,
  };
}

function structuredCompactionSummary(
  key: string,
  tokensBefore: number,
  tokensAfter: number,
  compacted: string,
): string {
  const context = truncateText(compacted, 650).trim();
  return [
    "## Goal",
    `Keep prompt component \`${key}\` resumable after semantic compaction.`,
    "",
    "## Progress",
    "### Done",
    `- Compacted \`${key}\` from ${tokensBefore} to ${tokensAfter} estimated tokens.`,
    "",
    "## Critical Context",
    context,
  ].join("\n").trim();
}

function compactComponent(key: string, text: string, maxTokens: number): string {
  if (HISTORY_COMPONENTS.has(key)) {
    const needsHistoryCompaction = text.split(/\r?\n/).length > 24 || splitSections(text).length > 4;
    if (!needsHistoryCompaction && estimateTokens(text) <= maxTokens) {
      return text;
    }
  } else if (estimateTokens(text) <= maxTokens) {
    return text;
  }

  let compacted: string;
  if (HISTORY_COMPONENTS.has(key)) {
    compacted = compactHistory(text, maxTokens);
  } else if (key === "trajectory") {
    compacted = compactTable(text, maxTokens);
  } else if (TAIL_PRESERVING_COMPONENTS.has(key) && looksLikePlainProse(text)) {
    compacted = compactPlainProse(text, maxTokens);
  } else if (key === "lessons") {
    compacted = compactMarkdown(text, maxTokens, true);
  } else {
    compacted = compactMarkdown(text, maxTokens, false);
  }

  if (estimateTokens(compacted) > maxTokens) {
    compacted = truncateText(compacted, maxTokens);
  }
  return compacted;
}

function compactHistory(text: string, maxTokens: number): string {
  const sections = splitSections(text);
  if (sections.length === 0) {
    return truncateText(text, maxTokens);
  }

  const selected = sections.slice(-4);
  const compacted = selected
    .map((section) => compactSection(section, false))
    .filter((section) => section.trim())
    .join("\n\n")
    .trim();
  if (compacted && compacted !== text) {
    return `${compacted}\n\n[... condensed recent history ...]`;
  }
  return compacted || truncateText(text, maxTokens);
}

function compactMarkdown(text: string, maxTokens: number, preferRecent: boolean): string {
  const sections = splitSections(text);
  if (sections.length === 0) {
    return truncateText(text, maxTokens);
  }

  const selectedSections = preferRecent ? sections.slice(-6) : sections.slice(0, 6);
  const compacted = selectedSections
    .map((section) => compactSection(section, preferRecent))
    .filter((section) => section.trim())
    .join("\n\n")
    .trim();
  if (compacted && compacted !== text) {
    return `${compacted}\n\n[... condensed structured context ...]`;
  }
  return compacted || truncateText(text, maxTokens);
}

function compactTable(text: string, maxTokens: number): string {
  const lines = text.split(/\r?\n/).map((line) => line.trimEnd());
  if (lines.length <= 12 && estimateTokens(text) <= maxTokens) {
    return text;
  }

  const tableHeader: string[] = [];
  const tableRows: string[] = [];
  const preTableLines: string[] = [];
  const postTableLines: string[] = [];
  let inTable = false;
  let sawTable = false;

  for (const line of lines) {
    if (line.startsWith("|")) {
      inTable = true;
      sawTable = true;
      if (tableHeader.length < 2) {
        tableHeader.push(line);
      } else {
        tableRows.push(line);
      }
    } else if (inTable && !line.trim()) {
      inTable = false;
    } else {
      const target = sawTable && !inTable ? postTableLines : preTableLines;
      target.push(line);
    }
  }

  const trailingContext = postTableLines.filter((line) => line.trim()).join("\n").trim();
  const compactedTrailingContext = trailingContext
    ? compactMarkdown(trailingContext, maxTokens, false)
    : "";
  const compactedLines = [
    ...preTableLines.slice(0, 4),
    ...tableHeader,
    ...tableRows.slice(-8),
  ];
  if (compactedTrailingContext) {
    compactedLines.push("", compactedTrailingContext);
  }
  const compacted = compactedLines.join("\n").trim();
  if (compacted && compacted !== text) {
    return `${compacted}\n\n[... condensed trajectory ...]`;
  }
  return compacted || truncateText(text, maxTokens);
}

function compactPlainProse(text: string, maxTokens: number): string {
  const lines = text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  if (lines.length === 0) {
    return truncateText(text, maxTokens);
  }

  const selected = dedupeLines([...lines.slice(0, 2), ...lines.slice(-3)]);
  const compacted = selected.join("\n").trim();
  if (compacted && compacted !== text) {
    return `${compacted}\n\n[... condensed recent context ...]`;
  }
  return compacted || truncateText(text, maxTokens);
}

function splitSections(text: string): string[] {
  if (text.includes("\n\n---\n\n")) {
    return text.split("\n\n---\n\n").map((section) => section.trim()).filter(Boolean);
  }

  const sections: string[][] = [];
  let current: string[] = [];
  for (const line of text.split(/\r?\n/)) {
    if (/^#{1,6}\s+/.test(line) && current.length > 0) {
      sections.push(current);
      current = [line];
      continue;
    }
    current.push(line);
  }
  if (current.length > 0) {
    sections.push(current);
  }

  return sections
    .filter((section) => section.some((line) => line.trim()))
    .map((section) => section.join("\n").trim());
}

function looksLikePlainProse(text: string): boolean {
  const stripped = text.trim();
  if (!stripped) return false;
  if (/^#{1,6}\s+/m.test(stripped)) return false;
  if (stripped.includes("\n\n---\n\n")) return false;
  if (/^\s*(?:[-*]|\d+\.)\s+/m.test(stripped)) return false;
  return true;
}

function compactSection(section: string, preferRecent: boolean): string {
  const lines = section.split(/\r?\n/).map((line) => line.trimEnd()).filter((line) => line.trim());
  if (lines.length === 0) {
    return "";
  }

  const selected: string[] = [];
  const bodyCandidates: string[] = [];
  let headingKept = false;

  for (const line of lines) {
    const stripped = line.trim();
    const normalized = stripped.toLowerCase();
    if (stripped.startsWith("#")) {
      if (!headingKept) {
        selected.push(stripped);
        headingKept = true;
      }
      continue;
    }
    if (
      isStructuredLine(stripped)
      || IMPORTANT_KEYWORDS.some((keyword) => normalized.includes(keyword))
    ) {
      bodyCandidates.push(stripped);
    }
  }

  const fallbackCandidates = lines.slice(1, 3).map((line) => line.trim()).filter(Boolean);
  const candidates = bodyCandidates.length > 0
    ? bodyCandidates
    : fallbackCandidates.length > 0
      ? fallbackCandidates
      : [lines[0].trim()];

  const dedupedCandidates = dedupeLines(candidates);
  const chosenCandidates = preferRecent ? dedupedCandidates.slice(-4) : dedupedCandidates.slice(0, 4);
  selected.push(...chosenCandidates);
  return selected.join("\n").trim();
}

function isStructuredLine(line: string): boolean {
  return (
    line.startsWith("- ")
    || line.startsWith("* ")
    || line.startsWith("> ")
    || /^\d+\.\s+/.test(line)
    || line.includes(":")
  );
}

function dedupeLines(lines: string[]): string[] {
  const deduped: string[] = [];
  const seen = new Set<string>();
  for (const line of lines) {
    const normalized = line.replace(/\s+/g, " ").trim().toLowerCase();
    if (!normalized || seen.has(normalized)) {
      continue;
    }
    seen.add(normalized);
    deduped.push(line.trim());
  }
  return deduped;
}

function truncateText(text: string, maxTokens: number): string {
  if (maxTokens <= 0) {
    return "";
  }
  const maxChars = maxTokens * 4;
  if (text.length <= maxChars) {
    return text;
  }
  let truncated = text.slice(0, maxChars).trimEnd();
  const lastNewline = truncated.lastIndexOf("\n");
  if (lastNewline > Math.floor(maxChars / 2)) {
    truncated = truncated.slice(0, lastNewline).trimEnd();
  }
  return `${truncated}\n[... condensed for prompt budget ...]`;
}

function newEntryId(): string {
  return randomBytes(4).toString("hex");
}

function utcTimestamp(): string {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}
