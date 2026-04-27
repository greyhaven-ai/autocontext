const CLASSIFIER_DESCRIPTION_SKIP_SECTIONS = new Set([
  "Why This Matters",
  "What This Tests",
  "Implementation Guidance",
  "Acceptance",
  "Why existing scenarios don't cover this",
  "Dependencies",
]);

const CLASSIFIER_DESCRIPTION_SKIP_LINE_PREFIXES = [
  "**Priority:**",
  "**Generations to signal:**",
] as const;

const CLASSIFIER_INLINE_EXAMPLE_PAREN_RE =
  /\(\s*(?:e\.g\.,?|eg,?|for example,?)[^)]*\)/gi;

export function buildFamilyClassificationBrief(description: string): string {
  const lines: string[] = [];
  let skippingSection = false;

  for (const rawLine of description.split(/\r?\n/)) {
    const headingMatch = /^\s*#{2,6}\s+(.+?)\s*$/.exec(rawLine);
    if (headingMatch) {
      const title = headingMatch[1]?.trim() ?? "";
      skippingSection = CLASSIFIER_DESCRIPTION_SKIP_SECTIONS.has(title);
      if (!skippingSection) {
        lines.push(rawLine);
      }
      continue;
    }

    const stripped = rawLine.trim();
    if (CLASSIFIER_DESCRIPTION_SKIP_LINE_PREFIXES.some((prefix) => stripped.startsWith(prefix))) {
      continue;
    }
    if (!skippingSection) {
      lines.push(rawLine);
    }
  }

  const brief = lines
    .join("\n")
    .trim()
    .replace(CLASSIFIER_INLINE_EXAMPLE_PAREN_RE, "")
    .replace(/\n{3,}/g, "\n\n")
    .replace(/[ \t]{2,}/g, " ");

  return brief || description.trim();
}
