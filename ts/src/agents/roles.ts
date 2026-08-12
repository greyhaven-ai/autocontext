/**
 * Role definitions, output contracts, and parsers (AC-345 Task 13).
 * Mirrors Python's autocontext/agents/contracts.py + parsers.py.
 */

import { parseArchitectHarnessSpecs } from "../execution/harness-loader.js";

// ---------------------------------------------------------------------------
// Role constants
// ---------------------------------------------------------------------------

export const ROLES = [
  "competitor",
  "translator",
  "analyst",
  "coach",
  "architect",
  "curator",
] as const;

export type Role = (typeof ROLES)[number];

// ---------------------------------------------------------------------------
// Output contracts
// ---------------------------------------------------------------------------

export interface CompetitorOutput {
  rawText: string;
  strategy: Record<string, unknown>;
  reasoning: string;
  isCodeStrategy: boolean;
}

export interface AnalystOutput {
  rawMarkdown: string;
  findings: string[];
  rootCauses: string[];
  recommendations: string[];
  parseSuccess: boolean;
}

export interface CoachOutput {
  rawMarkdown: string;
  playbook: string;
  lessons: string;
  hints: string;
  parseSuccess: boolean;
}

export interface ArchitectOutput {
  rawMarkdown: string;
  toolSpecs: Array<Record<string, unknown>>;
  harnessSpecs: Array<Record<string, unknown>>;
  changelogEntry: string;
  parseSuccess: boolean;
}

// ---------------------------------------------------------------------------
// Utility: extract delimited section
// ---------------------------------------------------------------------------

export function extractDelimitedSection(
  text: string,
  startMarker: string,
  endMarker: string,
): string | null {
  const startIdx = text.indexOf(startMarker);
  if (startIdx === -1) return null;
  const contentStart = startIdx + startMarker.length;
  const endIdx = text.indexOf(endMarker, contentStart);
  if (endIdx === -1) return null;
  return text.slice(contentStart, endIdx).trim();
}

// ---------------------------------------------------------------------------
// Parsers
// ---------------------------------------------------------------------------

function extractSectionBullets(markdown: string, heading: string): string[] {
  const escaped = heading.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const pattern = new RegExp(`^##\\s+${escaped}\\s*$`, "m");
  const match = pattern.exec(markdown);
  if (!match) return [];

  const after = markdown.slice(match.index + match[0].length);
  const bullets: string[] = [];
  for (const line of after.split("\n")) {
    const stripped = line.trim();
    if (stripped.startsWith("#")) break;
    if (stripped.startsWith("- ")) {
      bullets.push(stripped.slice(2).trim());
    }
  }
  return bullets;
}

export function parseCompetitorOutput(
  rawText: string,
  strategy: Record<string, unknown>,
  isCodeStrategy = false,
): CompetitorOutput {
  return {
    rawText,
    strategy,
    reasoning: rawText.trim(),
    isCodeStrategy,
  };
}

export function parseAnalystOutput(rawMarkdown: string): AnalystOutput {
  try {
    return {
      rawMarkdown,
      findings: extractSectionBullets(rawMarkdown, "Findings"),
      rootCauses: extractSectionBullets(rawMarkdown, "Root Causes"),
      recommendations: extractSectionBullets(rawMarkdown, "Actionable Recommendations"),
      parseSuccess: true,
    };
  } catch {
    return { rawMarkdown, findings: [], rootCauses: [], recommendations: [], parseSuccess: false };
  }
}

export function parseCoachOutput(rawMarkdown: string): CoachOutput {
  try {
    const playbook = extractDelimitedSection(
      rawMarkdown,
      "<!-- PLAYBOOK_START -->",
      "<!-- PLAYBOOK_END -->",
    );
    const lessons = extractDelimitedSection(
      rawMarkdown,
      "<!-- LESSONS_START -->",
      "<!-- LESSONS_END -->",
    );
    const hints = extractDelimitedSection(
      rawMarkdown,
      "<!-- COMPETITOR_HINTS_START -->",
      "<!-- COMPETITOR_HINTS_END -->",
    );
    // AC-904: START without END is the truncation signature; the fragment
    // must not become the playbook. No markers at all keeps the legacy
    // whole-content fallback (mirrors Python parse_coach_sections).
    let effectivePlaybook: string;
    let parseSuccess = true;
    if (playbook !== null && playbook !== undefined) {
      effectivePlaybook = playbook;
    } else if (rawMarkdown.includes("<!-- PLAYBOOK_START -->")) {
      effectivePlaybook = "";
      parseSuccess = false;
    } else {
      effectivePlaybook = rawMarkdown.trim();
    }
    return {
      rawMarkdown,
      playbook: effectivePlaybook,
      lessons: lessons ?? "",
      hints: hints ?? "",
      parseSuccess,
    };
  } catch {
    return { rawMarkdown, playbook: "", lessons: "", hints: "", parseSuccess: false };
  }
}

export function parseArchitectOutput(rawMarkdown: string): ArchitectOutput {
  try {
    const toolSpecs = parseArchitectToolSpecs(rawMarkdown);
    const harnessSpecs = parseArchitectHarnessSpecs(rawMarkdown).map((spec) => ({ ...spec }));
    return {
      rawMarkdown,
      toolSpecs,
      // TypeScript does not execute the proposed Python validators, but this is
      // a public parser contract. Keep the validated specs as opaque proposal
      // data so callers can inspect, persist, or forward them without loss.
      harnessSpecs,
      changelogEntry: "",
      parseSuccess: true,
    };
  } catch {
    return {
      rawMarkdown,
      toolSpecs: [],
      harnessSpecs: [],
      changelogEntry: "",
      parseSuccess: false,
    };
  }
}

/**
 * Extract proposed tool specs. Deliberately does NOT syntax-check `code`.
 *
 * Like Python's `parse_architect_tool_specs`, this filters malformed entries
 * and keeps only string name/description/code fields. Python's `ast.parse` on
 * tool code runs later at the persistence boundary (`storage/artifacts.py`, on
 * the `kind="tool"` path), immediately before the source is written to a `.py`
 * file that something will import.
 *
 * TypeScript has no internal persistence or execution path for proposed tool
 * code. Adding a syntax check here would therefore make this parser stricter
 * than Python's equivalent stage. If such a path is added, validate syntax at
 * that boundary.
 */
function parseArchitectToolSpecs(markdown: string): Array<Record<string, unknown>> {
  const codeBlockPattern = /```json\s*\n([\s\S]*?)\n```/g;
  let match: RegExpExecArray | null;
  while ((match = codeBlockPattern.exec(markdown)) !== null) {
    try {
      const parsed: unknown = JSON.parse(match[1]);
      if (
        typeof parsed === "object" &&
        parsed !== null &&
        "tools" in parsed &&
        Array.isArray(parsed.tools)
      ) {
        const tools: unknown[] = parsed.tools;
        return tools
          .filter(isArchitectToolSpec)
          .map(({ name, description, code }) => ({ name, description, code }));
      }
    } catch {
      continue;
    }
  }
  return [];
}

function isArchitectToolSpec(
  value: unknown,
): value is { name: string; description: string; code: string } {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return false;
  }
  return (
    "name" in value &&
    typeof value.name === "string" &&
    "description" in value &&
    typeof value.description === "string" &&
    "code" in value &&
    typeof value.code === "string"
  );
}
