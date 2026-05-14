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

export interface ComponentBudgetHit {
  component: string;
  beforeTokens: number;
  afterTokens: number;
}

export interface ComponentCapHit extends ComponentBudgetHit {
  capTokens: number;
}

export interface GlobalTrimHit extends ComponentBudgetHit {
  targetTokens: number;
}

export interface ContextBudgetTelemetry {
  maxTokens: number;
  inputTokenEstimate: number;
  outputTokenEstimate: number;
  tokenReduction: number;
  componentTokensBefore: Record<string, number>;
  componentTokensAfter: Record<string, number>;
  dedupeHitCount: number;
  deduplicatedComponents: string[];
  roleScopedDedupeSkipCount: number;
  protectedDedupeSkipCount: number;
  componentCapHitCount: number;
  componentCapHits: ComponentCapHit[];
  trimmedComponentCount: number;
  trimmedComponents: string[];
  globalTrimHits: GlobalTrimHit[];
}

export interface ContextBudgetResult {
  components: Record<string, string>;
  telemetry: ContextBudgetTelemetry;
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
    return this.applyWithTelemetry(components).components;
  }

  applyWithTelemetry(components: Record<string, string>): ContextBudgetResult {
    const inputComponents = { ...components };
    const componentTokensBefore = componentTokenCounts(inputComponents);
    const inputTokenEstimate = sumTokens(componentTokensBefore);
    if (this.maxTokens <= 0) {
      return {
        components: inputComponents,
        telemetry: buildTelemetry({
          maxTokens: this.maxTokens,
          inputTokenEstimate,
          componentTokensBefore,
          componentTokensAfter: { ...componentTokensBefore },
        }),
      };
    }

    const deduped = deduplicateEquivalentComponents(inputComponents, this.policy);
    const capped = applyComponentCaps(
      deduped.components,
      this.policy,
    );
    const result = capped.components;

    let total = 0;
    for (const v of Object.values(result)) {
      total += estimateTokens(v);
    }
    const globalTrimHits: GlobalTrimHit[] = [];
    let remaining = total;

    if (total > this.maxTokens) {
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
          globalTrimHits.push({
            component: key,
            beforeTokens: oldTokens,
            afterTokens: newTokens,
            targetTokens,
          });
        }
      }
    }

    return {
      components: result,
      telemetry: buildTelemetry({
        maxTokens: this.maxTokens,
        inputTokenEstimate,
        componentTokensBefore,
        componentTokensAfter: componentTokenCounts(result),
        deduplicatedComponents: deduped.deduplicatedComponents,
        roleScopedDedupeSkipCount: deduped.roleScopedDedupeSkipCount,
        protectedDedupeSkipCount: deduped.protectedDedupeSkipCount,
        componentCapHits: capped.componentCapHits,
        globalTrimHits,
      }),
    };
  }
}

interface TelemetryInput {
  maxTokens: number;
  inputTokenEstimate: number;
  componentTokensBefore: Record<string, number>;
  componentTokensAfter: Record<string, number>;
  deduplicatedComponents?: string[];
  roleScopedDedupeSkipCount?: number;
  protectedDedupeSkipCount?: number;
  componentCapHits?: ComponentCapHit[];
  globalTrimHits?: GlobalTrimHit[];
}

function buildTelemetry(input: TelemetryInput): ContextBudgetTelemetry {
  const componentCapHits = input.componentCapHits ?? [];
  const globalTrimHits = input.globalTrimHits ?? [];
  const outputTokenEstimate = sumTokens(input.componentTokensAfter);
  return {
    maxTokens: input.maxTokens,
    inputTokenEstimate: input.inputTokenEstimate,
    outputTokenEstimate,
    tokenReduction: Math.max(0, input.inputTokenEstimate - outputTokenEstimate),
    componentTokensBefore: { ...input.componentTokensBefore },
    componentTokensAfter: { ...input.componentTokensAfter },
    dedupeHitCount: input.deduplicatedComponents?.length ?? 0,
    deduplicatedComponents: [...(input.deduplicatedComponents ?? [])],
    roleScopedDedupeSkipCount: input.roleScopedDedupeSkipCount ?? 0,
    protectedDedupeSkipCount: input.protectedDedupeSkipCount ?? 0,
    componentCapHitCount: componentCapHits.length,
    componentCapHits: componentCapHits.map((hit) => ({ ...hit })),
    trimmedComponentCount: globalTrimHits.length,
    trimmedComponents: globalTrimHits.map((hit) => hit.component),
    globalTrimHits: globalTrimHits.map((hit) => ({ ...hit })),
  };
}

function deduplicateEquivalentComponents(
  components: Record<string, string>,
  policy: ContextBudgetPolicy,
): {
  components: Record<string, string>;
  deduplicatedComponents: string[];
  roleScopedDedupeSkipCount: number;
  protectedDedupeSkipCount: number;
} {
  const groups = new Map<string, string[]>();
  let roleScopedDedupeSkipCount = 0;
  for (const [key, value] of Object.entries(components)) {
    if (policy.roleScopedComponents.has(key)) {
      if (duplicateKey(value)) roleScopedDedupeSkipCount += 1;
      continue;
    }
    const normalized = duplicateKey(value);
    if (!normalized) continue;
    groups.set(normalized, [...(groups.get(normalized) ?? []), key]);
  }

  const rank = canonicalRank(policy.canonicalComponentOrder);
  const deduplicatedComponents: string[] = [];
  let protectedDedupeSkipCount = 0;
  for (const keys of groups.values()) {
    if (keys.length < 2) continue;
    protectedDedupeSkipCount += keys.filter((key) => policy.protectedComponents.has(key)).length;
    const unprotected = keys.filter((key) => !policy.protectedComponents.has(key));
    if (unprotected.length === 0) continue;
    const keep = [...unprotected].sort((a, b) => rank(a) - rank(b))[0];
    for (const key of unprotected) {
      if (key !== keep) {
        components[key] = "";
        deduplicatedComponents.push(key);
      }
    }
  }
  return {
    components,
    deduplicatedComponents,
    roleScopedDedupeSkipCount,
    protectedDedupeSkipCount,
  };
}

function applyComponentCaps(
  components: Record<string, string>,
  policy: ContextBudgetPolicy,
): { components: Record<string, string>; componentCapHits: ComponentCapHit[] } {
  const result = { ...components };
  const componentCapHits: ComponentCapHit[] = [];
  for (const [key, cap] of Object.entries(policy.componentTokenCaps)) {
    if (!(key in result) || policy.protectedComponents.has(key)) continue;
    if (!Number.isFinite(cap)) continue;
    const value = result[key];
    const beforeTokens = estimateTokens(value);
    if (beforeTokens > cap) {
      result[key] = truncateToTokens(value, cap);
      componentCapHits.push({
        component: key,
        beforeTokens,
        afterTokens: estimateTokens(result[key]),
        capTokens: cap,
      });
    }
  }
  return { components: result, componentCapHits };
}

function duplicateKey(value: string): string {
  return value.split(/\s+/).filter(Boolean).join(" ");
}

function canonicalRank(order: readonly string[]): (key: string) => number {
  const ranks = new Map(order.map((key, index) => [key, index]));
  return (key: string) => ranks.get(key) ?? ranks.size;
}

function componentTokenCounts(components: Record<string, string>): Record<string, number> {
  return Object.fromEntries(
    Object.entries(components).map(([key, value]) => [key, estimateTokens(value)]),
  );
}

function sumTokens(counts: Record<string, number>): number {
  return Object.values(counts).reduce((total, value) => total + value, 0);
}
