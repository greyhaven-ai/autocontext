/**
 * Rubric coherence pre-check utility.
 * Detects potential issues in rubric text before judge evaluation.
 */

export interface RubricCoherenceResult {
  warnings: string[];
  isCoherent: boolean;
}

export function checkRubricCoherence(rubric: string): RubricCoherenceResult {
  const warnings: string[] = [];

  // Check for contradictory adjective pairs
  const contradictions: [string, string][] = [
    ["simple", "complex"],
    ["brief", "comprehensive"],
    ["concise", "detailed"],
    ["short", "thorough"],
    ["minimal", "extensive"],
  ];
  const lower = rubric.toLowerCase();
  for (const [a, b] of contradictions) {
    const aRe = new RegExp(`\\b${a}\\b`);
    const bRe = new RegExp(`\\b${b}\\b`);
    if (aRe.test(lower) && bRe.test(lower)) {
      warnings.push(`Potentially contradictory criteria: "${a}" and "${b}" both appear`);
    }
  }

  // Check for overly vague criteria
  const vaguePattern = /\b(good|nice|appropriate|adequate|proper)\b/gi;
  const vagueMatches = lower.match(vaguePattern);
  if (vagueMatches && vagueMatches.length > 2) {
    warnings.push(
      `Rubric may be too vague: ${vagueMatches.length} generic terms found (${vagueMatches.slice(0, 3).join(", ")})`,
    );
  }

  // Check for very short rubric (likely underspecified)
  if (rubric.trim().split(/\s+/).length < 10) {
    warnings.push("Rubric may be underspecified: fewer than 10 words");
  }

  return { warnings, isCoherent: warnings.length === 0 };
}
