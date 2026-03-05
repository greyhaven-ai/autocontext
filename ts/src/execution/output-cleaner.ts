/**
 * Strips revision metadata from agent outputs.
 *
 * LLM revision agents often prepend/append analysis headers, self-assessment,
 * and "Key Changes Made" sections alongside the actual revised content.
 * This inflates judge scores by mixing meta-commentary with the deliverable.
 */

/**
 * Strip from the last occurrence of `header` to the end of `text`.
 * Only triggers when `header` appears at a newline boundary (or start of string).
 */
function stripLastSection(text: string, header: string): string {
  if (text.startsWith(header)) return "";
  const idx = text.lastIndexOf(`\n${header}`);
  if (idx !== -1) return text.slice(0, idx);
  return text;
}

/**
 * Remove common revision metadata patterns from LLM output.
 *
 * Strips:
 * - `## Revised Output` header at the start
 * - `## Key Changes Made` and everything after
 * - `**Analysis:**` and everything after
 * - `## Analysis`, `## Changes`, `## Improvements`, `## Self-Assessment` sections
 *   (from the *last* occurrence only, to avoid destroying legitimate content)
 * - Trailing "This revision transforms/improves/addresses/fixes..." paragraphs
 */
export function cleanRevisionOutput(output: string): string {
  let cleaned = output;

  // Strip "## Revised Output" header at the start
  cleaned = cleaned.replace(/^## Revised Output\s*\n/, "");

  // Unambiguous metadata headers — always strip from first occurrence
  const unambiguousPatterns = [
    /(?:^|\n)## Key Changes Made[\s\S]*/,
    /(?:^|\n)\*\*Analysis:\*\*[\s\S]*/,
    /(?:^|\n)## Self-Assessment[\s\S]*/,
  ];
  for (const pattern of unambiguousPatterns) {
    cleaned = cleaned.replace(pattern, "");
  }

  // Ambiguous headers — only strip from the last occurrence to preserve
  // legitimate content that may use the same heading earlier
  for (const header of ["## Analysis", "## Changes", "## Improvements"]) {
    cleaned = stripLastSection(cleaned, header);
  }

  // Strip trailing meta-paragraphs starting with "This revision ..."
  cleaned = cleaned.replace(
    /(?:^|\n)This revision (?:transforms|improves|addresses|fixes)[\s\S]*$/,
    "",
  );

  return cleaned.trim();
}
