/**
 * Strips revision metadata from agent outputs.
 *
 * LLM revision agents often prepend/append analysis headers, self-assessment,
 * and "Key Changes Made" sections alongside the actual revised content.
 * This inflates judge scores by mixing meta-commentary with the deliverable.
 */

/**
 * Remove common revision metadata patterns from LLM output.
 *
 * Strips:
 * - `## Revised Output` header at the start
 * - `## Key Changes Made` and everything after
 * - `**Analysis:**` and everything after
 * - `## Analysis`, `## Changes`, `## Improvements`, `## Self-Assessment` sections
 * - Trailing "This revision transforms/improves/addresses/fixes..." paragraphs
 */
export function cleanRevisionOutput(output: string): string {
  let cleaned = output;

  // Strip "## Revised Output" header at the start
  cleaned = cleaned.replace(/^## Revised Output\s*\n/, "");

  // Strip trailing sections — match at newline boundary or start of string
  const trailingSections = [
    /(?:^|\n)## Key Changes Made[\s\S]*/,
    /(?:^|\n)\*\*Analysis:\*\*[\s\S]*/,
    /(?:^|\n)## Analysis[\s\S]*/,
    /(?:^|\n)## Changes[\s\S]*/,
    /(?:^|\n)## Improvements[\s\S]*/,
    /(?:^|\n)## Self-Assessment[\s\S]*/,
  ];

  for (const pattern of trailingSections) {
    cleaned = cleaned.replace(pattern, "");
  }

  // Strip trailing meta-paragraphs starting with "This revision ..."
  cleaned = cleaned.replace(
    /(?:^|\n)This revision (?:transforms|improves|addresses|fixes)[\s\S]*$/,
    "",
  );

  return cleaned.trim();
}
