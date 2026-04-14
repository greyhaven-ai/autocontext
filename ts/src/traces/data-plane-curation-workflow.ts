import type {
  CuratedDataset,
  CurationPolicy,
  TraceEntry,
} from "./data-plane-types.js";

export interface NormalizedCurationPolicy {
  minScore: number;
  heldOutRatio: number;
  requireTrainingConsent: boolean;
}

export function normalizeCurationPolicy(
  policy?: CurationPolicy,
): NormalizedCurationPolicy {
  return {
    minScore: policy?.minScore ?? 0,
    heldOutRatio: policy?.heldOutRatio ?? 0,
    requireTrainingConsent: policy?.requireTrainingConsent ?? true,
  };
}

export function shouldIncludeTraceEntry(
  entry: TraceEntry,
  policy: NormalizedCurationPolicy,
): boolean {
  if (policy.requireTrainingConsent && !entry.attestation.allowTraining) {
    return false;
  }

  const score = (entry.trace.outcome as { score?: number } | undefined)?.score;
  if (score != null && score < policy.minScore) {
    return false;
  }

  return true;
}

export function splitHeldOutTraceEntries(
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

export function curateTraceEntries(
  entries: TraceEntry[],
  policy: NormalizedCurationPolicy,
): CuratedDataset {
  const included: TraceEntry[] = [];
  const excluded: TraceEntry[] = [];

  for (const entry of entries) {
    if (shouldIncludeTraceEntry(entry, policy)) {
      included.push(entry);
    } else {
      excluded.push(entry);
    }
  }

  const split = splitHeldOutTraceEntries(included, policy.heldOutRatio);
  return {
    included,
    excluded,
    train: split.train,
    heldOut: split.heldOut,
  };
}
