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
  "evidence_manifest",
  "evidence_manifest_analyst",
  "evidence_manifest_architect",
  "notebook_architect",
  "notebook_coach",
  "notebook_analyst",
  "notebook_competitor",
  "experiment_log",
  "research_protocol",
  "environment_snapshot",
  "trajectory",
  "analysis",
  "tools",
  "lessons",
  "playbook",
] as const;

// Components that are never trimmed
const PROTECTED = new Set(["hints", "dead_ends"]);

// Components that belong to separate final role prompts. They may share text
// without being duplicate context inside any one prompt.
const ROLE_SCOPED_COMPONENTS = new Set([
  "evidence_manifest_analyst",
  "evidence_manifest_architect",
  "notebook_competitor",
  "notebook_analyst",
  "notebook_coach",
  "notebook_architect",
]);

const TRUNCATION_MARKER = "\n[... truncated for context budget ...]";

const CANONICAL_COMPONENT_ORDER = [
  "hints",
  "dead_ends",
  "playbook",
  "lessons",
  "analysis",
  "trajectory",
  "tools",
  "session_reports",
  "research_protocol",
  "experiment_log",
  "environment_snapshot",
  "evidence_manifest",
  "evidence_manifest_analyst",
  "evidence_manifest_architect",
  "notebook_competitor",
  "notebook_analyst",
  "notebook_coach",
  "notebook_architect",
] as const;

const COMPONENT_TOKEN_CAPS: Record<string, number> = {
  playbook: 2800,
  lessons: 1600,
  analysis: 1800,
  trajectory: 1200,
  tools: 1400,
  experiment_log: 1800,
  research_protocol: 1200,
  session_reports: 1400,
  environment_snapshot: 1200,
  evidence_manifest: 1200,
  evidence_manifest_analyst: 1200,
  evidence_manifest_architect: 1200,
  notebook_competitor: 800,
  notebook_analyst: 800,
  notebook_coach: 800,
  notebook_architect: 800,
};

export interface ContextBudgetPolicyOptions {
  trimOrder?: readonly string[];
  protectedComponents?: Iterable<string>;
  roleScopedComponents?: Iterable<string>;
  componentTokenCaps?: Record<string, number>;
  canonicalComponentOrder?: readonly string[];
}

export class ContextBudgetPolicy {
  readonly trimOrder: readonly string[];
  readonly protectedComponents: ReadonlySet<string>;
  readonly roleScopedComponents: ReadonlySet<string>;
  readonly componentTokenCaps: Record<string, number>;
  readonly canonicalComponentOrder: readonly string[];

  constructor(opts: ContextBudgetPolicyOptions = {}) {
    this.trimOrder = [...(opts.trimOrder ?? TRIM_ORDER)];
    this.protectedComponents = new Set(opts.protectedComponents ?? PROTECTED);
    this.roleScopedComponents = new Set(opts.roleScopedComponents ?? ROLE_SCOPED_COMPONENTS);
    this.componentTokenCaps = { ...(opts.componentTokenCaps ?? COMPONENT_TOKEN_CAPS) };
    this.canonicalComponentOrder = [...(opts.canonicalComponentOrder ?? CANONICAL_COMPONENT_ORDER)];
  }
}

export function estimateTokens(text: string): number {
  return Math.floor(text.length / 4);
}

function truncateToTokens(text: string, maxTokens: number): string {
  if (maxTokens <= 0) return "";
  const maxChars = maxTokens * 4 + 3;
  if (text.length <= maxChars) return text;
  if (TRUNCATION_MARKER.length > maxChars) return text.slice(0, maxChars);
  const prefixChars = maxChars - TRUNCATION_MARKER.length;
  let truncated = text.slice(0, prefixChars);
  const lastNl = truncated.lastIndexOf("\n");
  if (lastNl > prefixChars / 2) {
    truncated = truncated.slice(0, lastNl);
  }
  return truncated + TRUNCATION_MARKER;
}

export class ContextBudget {
  private maxTokens: number;
  private policy: ContextBudgetPolicy;

  constructor(maxTokens = 100_000, policy = new ContextBudgetPolicy()) {
    this.maxTokens = maxTokens;
    this.policy = policy;
  }

  apply(components: Record<string, string>): Record<string, string> {
    if (this.maxTokens <= 0) return { ...components };

    const result = applyComponentCaps(
      deduplicateEquivalentComponents({ ...components }, this.policy),
      this.policy,
    );

    let total = 0;
    for (const v of Object.values(result)) {
      total += estimateTokens(v);
    }
    if (total <= this.maxTokens) return result;

    let remaining = total;

    for (const key of this.policy.trimOrder) {
      if (!(key in result) || this.policy.protectedComponents.has(key)) continue;
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

function deduplicateEquivalentComponents(
  components: Record<string, string>,
  policy: ContextBudgetPolicy,
): Record<string, string> {
  const groups = new Map<string, string[]>();
  for (const [key, value] of Object.entries(components)) {
    if (policy.roleScopedComponents.has(key)) continue;
    const normalized = duplicateKey(value);
    if (!normalized) continue;
    groups.set(normalized, [...(groups.get(normalized) ?? []), key]);
  }

  const rank = canonicalRank(policy.canonicalComponentOrder);
  for (const keys of groups.values()) {
    if (keys.length < 2) continue;
    const unprotected = keys.filter((key) => !policy.protectedComponents.has(key));
    if (unprotected.length === 0) continue;
    const keep = [...unprotected].sort((a, b) => rank(a) - rank(b))[0];
    for (const key of unprotected) {
      if (key !== keep) {
        components[key] = "";
      }
    }
  }
  return components;
}

function applyComponentCaps(
  components: Record<string, string>,
  policy: ContextBudgetPolicy,
): Record<string, string> {
  const result = { ...components };
  for (const [key, cap] of Object.entries(policy.componentTokenCaps)) {
    if (!(key in result) || policy.protectedComponents.has(key)) continue;
    if (!Number.isFinite(cap)) continue;
    const value = result[key];
    if (estimateTokens(value) > cap) {
      result[key] = truncateToTokens(value, cap);
    }
  }
  return result;
}

function duplicateKey(value: string): string {
  return value.split(/\s+/).filter(Boolean).join(" ");
}

function canonicalRank(order: readonly string[]): (key: string) => number {
  const ranks = new Map(order.map((key, index) => [key, index]));
  return (key: string) => ranks.get(key) ?? ranks.size;
}
