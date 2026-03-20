/**
 * Context budget management for prompt assembly (AC-344 Task 12).
 * Mirrors Python's autocontext/prompts/context_budget.py.
 *
 * Uses char/4 heuristic for token estimation (no tokenizer dependency).
 * Progressive trim cascade from least critical to most critical.
 * Hints and dead_ends are never trimmed.
 */

// Trim cascade: first entry trimmed first (least critical)
const TRIM_ORDER = [
  "session_reports",
  "notebook_architect",
  "notebook_coach",
  "notebook_analyst",
  "notebook_competitor",
  "experiment_log",
  "research_protocol",
  "trajectory",
  "analysis",
  "tools",
  "lessons",
  "playbook",
] as const;

// Components that are never trimmed
const PROTECTED = new Set(["hints", "dead_ends"]);

export function estimateTokens(text: string): number {
  return Math.floor(text.length / 4);
}

function truncateToTokens(text: string, maxTokens: number): string {
  if (maxTokens <= 0) return "";
  const maxChars = maxTokens * 4;
  if (text.length <= maxChars) return text;
  let truncated = text.slice(0, maxChars);
  const lastNl = truncated.lastIndexOf("\n");
  if (lastNl > maxChars / 2) {
    truncated = truncated.slice(0, lastNl);
  }
  return truncated + "\n[... truncated for context budget ...]";
}

export class ContextBudget {
  private maxTokens: number;

  constructor(maxTokens = 100_000) {
    this.maxTokens = maxTokens;
  }

  apply(components: Record<string, string>): Record<string, string> {
    if (this.maxTokens <= 0) return { ...components };

    let total = 0;
    for (const v of Object.values(components)) {
      total += estimateTokens(v);
    }
    if (total <= this.maxTokens) return { ...components };

    const result = { ...components };
    let remaining = total;

    for (const key of TRIM_ORDER) {
      if (!(key in result) || PROTECTED.has(key)) continue;
      if (remaining <= this.maxTokens) break;

      const overshoot = remaining - this.maxTokens;
      const oldTokens = estimateTokens(result[key]);
      const targetTokens = Math.max(0, oldTokens - overshoot);

      if (targetTokens < oldTokens) {
        result[key] = truncateToTokens(result[key], targetTokens);
        const newTokens = estimateTokens(result[key]);
        remaining -= oldTokens - newTokens;
      }
    }

    return result;
  }
}
