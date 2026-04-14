import type {
  DistillationBuildBuckets,
  DistillationPolicy,
  FailurePolicy,
  TraceEntry,
} from "./distillation-types.js";

export interface NormalizedDistillationPolicy {
  minScore: number;
  topQuartile: boolean;
  advanceOnly: boolean;
  familyFilter: string[];
  heldOutRatio: number;
  failurePolicy: FailurePolicy;
  requireTrainingConsent: boolean;
}

export function normalizeDistillationPolicy(
  policy?: DistillationPolicy,
): NormalizedDistillationPolicy {
  return {
    minScore: policy?.minScore ?? 0,
    topQuartile: policy?.topQuartile ?? false,
    advanceOnly: policy?.advanceOnly ?? false,
    familyFilter: policy?.familyFilter ?? [],
    heldOutRatio: policy?.heldOutRatio ?? 0,
    failurePolicy: policy?.failurePolicy ?? "exclude",
    requireTrainingConsent: policy?.requireTrainingConsent ?? true,
  };
}

export function computeTopQuartileThreshold(entries: TraceEntry[]): number {
  const scores = entries
    .map((entry) => (entry.trace.outcome as Record<string, unknown> | undefined)?.score)
    .filter((score): score is number => typeof score === "number")
    .sort((left, right) => left - right);

  if (scores.length === 0) {
    return 0;
  }
  const q75Index = Math.floor(scores.length * 0.75);
  return scores[q75Index] ?? scores[scores.length - 1];
}

export function applyDistillationPolicy(
  entries: TraceEntry[],
  policy: NormalizedDistillationPolicy,
): DistillationBuildBuckets {
  let candidates = entries;

  if (policy.requireTrainingConsent) {
    candidates = candidates.filter((entry) => entry.attestation.allowTraining);
  }

  if (policy.advanceOnly) {
    candidates = candidates.filter((entry) => {
      const gate = (entry.trace.metadata as Record<string, unknown> | undefined)?.gateDecision;
      return gate === "advance";
    });
  }

  if (policy.familyFilter.length > 0) {
    const families = new Set(policy.familyFilter);
    candidates = candidates.filter((entry) => {
      const family = (entry.trace.metadata as Record<string, unknown> | undefined)?.family;
      return typeof family === "string" && families.has(family);
    });
  }

  const scoreThreshold = policy.topQuartile
    ? computeTopQuartileThreshold(candidates)
    : policy.minScore;

  const included: TraceEntry[] = [];
  const excluded: TraceEntry[] = [];
  const evalOnly: TraceEntry[] = [];
  const contrastive: TraceEntry[] = [];

  for (const entry of candidates) {
    const score = (entry.trace.outcome as Record<string, unknown> | undefined)?.score as number | undefined;
    const passes = score == null || score >= scoreThreshold;

    if (passes) {
      included.push(entry);
    } else if (policy.failurePolicy === "eval_only") {
      evalOnly.push(entry);
    } else if (policy.failurePolicy === "contrastive") {
      contrastive.push(entry);
    } else {
      excluded.push(entry);
    }
  }

  const allExcluded = [...excluded, ...entries.filter((entry) => !candidates.includes(entry))];
  return { included, excluded: allExcluded, evalOnly, contrastive };
}

export function splitHeldOutEntries(
  entries: TraceEntry[],
  heldOutRatio: number,
): { train: TraceEntry[]; heldOut: TraceEntry[] } {
  if (heldOutRatio <= 0 || entries.length <= 1) {
    return { train: [...entries], heldOut: [] };
  }

  const heldOutCount = Math.max(1, Math.floor(entries.length * heldOutRatio));
  return {
    train: entries.slice(0, entries.length - heldOutCount),
    heldOut: entries.slice(entries.length - heldOutCount),
  };
}

export function summarizeSources(entries: TraceEntry[]): Record<string, number> {
  const sources: Record<string, number> = {};
  for (const entry of entries) {
    const source = entry.manifest.sourceHarness;
    sources[source] = (sources[source] ?? 0) + 1;
  }
  return sources;
}
