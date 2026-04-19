/**
 * Composable trace selection for dataset generation (spec §8.2).
 *
 * Rules are applied in order; each rule transforms the trace set forward.
 * Typical pipeline: `gate → top-quartile → split`, or `gate → contrastive
 * → split`.
 *
 * Rule variants:
 *   - `gate`:         include[] AND / exclude[] filters over MatchExpressions.
 *   - `top-quartile`: rank by a numeric JSON-path; take top N%. Per-cluster
 *                     ranking is supported when the caller provides cluster
 *                     keys (see `applySelectionRulesPerCluster`).
 *   - `contrastive`:  pair failures to successes within the same cluster.
 *   - `split`:        partition into train/eval/holdout. Deterministic given
 *                     seed + shuffle flag.
 *
 * The split rule is the exit point of the pipeline — subsequent rules have
 * no access to the split buckets. We do NOT attach split labels here: the
 * `split.ts` helper is the authoritative partitioner. Including `split` as
 * a SelectionRule variant here is for config-file symmetry (the existing
 * spec §8.2 format), but the orchestrator pulls split configuration out
 * before applying the rest of the rules.
 */
import type { ProductionTrace } from "../contract/types.js";
import type {
  ContrastiveRule,
  GateRule,
  MatchExpression,
  SelectionRule,
  SplitRule,
  TopQuartileRule,
} from "./types.js";
import { matchExpression, resolveJsonPath } from "./cluster.js";
import { seededShuffle } from "./split.js";

export type TracePair = readonly [ProductionTrace, ProductionTrace];

export interface SelectionResult {
  readonly rows: readonly ProductionTrace[];
  readonly pairs?: readonly TracePair[];
}

/**
 * Apply a list of selection rules in order over a flat trace list. Returns
 * the filtered rows (and pair output when a contrastive rule runs).
 *
 * Split rules in the middle of the pipeline are treated as a no-op here —
 * the orchestrator extracts the split rule and runs it after assembling
 * rows (see `pipeline.ts`).
 */
export function applySelectionRules(
  traces: readonly ProductionTrace[],
  rules: readonly SelectionRule[],
  seed: number,
): SelectionResult {
  let current: readonly ProductionTrace[] = traces;
  let pairs: readonly TracePair[] | undefined;

  for (const rule of rules) {
    switch (rule.type) {
      case "gate":
        current = applyGate(current, rule);
        break;
      case "top-quartile":
        // Flat mode: treat the whole input as one cluster.
        current = applyTopQuartile(current, rule);
        break;
      case "contrastive":
        {
          const res = applyContrastive(current, rule, inferClusterKey);
          current = res.rows;
          pairs = res.pairs;
        }
        break;
      case "split":
        // Split is handled by the orchestrator — no-op here.
        break;
    }
    void seed; // reserved for future within-rule randomization
  }
  if (pairs !== undefined) return { rows: current, pairs };
  return { rows: current };
}

/**
 * Apply selection rules over pre-clustered traces. `top-quartile` with
 * `perCluster: true` is handled here; `contrastive` uses the cluster key
 * directly as the `taskCluster`. The orchestrator calls this.
 */
export function applySelectionRulesPerCluster(
  clusterTraces: ReadonlyMap<string, readonly ProductionTrace[]>,
  rules: readonly SelectionRule[],
  seed: number,
): Map<string, SelectionResult> {
  const out = new Map<string, SelectionResult>();
  for (const [clusterId, traces] of clusterTraces) {
    let current: readonly ProductionTrace[] = traces;
    let pairs: readonly TracePair[] | undefined;
    for (const rule of rules) {
      switch (rule.type) {
        case "gate":
          current = applyGate(current, rule);
          break;
        case "top-quartile":
          current = applyTopQuartile(current, rule);
          break;
        case "contrastive":
          {
            const res = applyContrastive(current, rule, () => clusterId);
            current = res.rows;
            pairs = res.pairs;
          }
          break;
        case "split":
          break;
      }
      void seed;
    }
    out.set(clusterId, pairs !== undefined ? { rows: current, pairs } : { rows: current });
  }
  return out;
}

// ---- Gate ------------------------------------------------------------------

function applyGate(
  traces: readonly ProductionTrace[],
  rule: GateRule,
): readonly ProductionTrace[] {
  const includes = rule.include ?? [];
  const excludes = rule.exclude ?? [];
  return traces.filter((t) => {
    // `include[]` is AND: every include must match (if list non-empty).
    for (const e of includes) {
      if (!matchExpression(t, e as MatchExpression)) return false;
    }
    // `exclude[]` is OR: any match excludes the trace.
    for (const e of excludes) {
      if (matchExpression(t, e as MatchExpression)) return false;
    }
    return true;
  });
}

// ---- Top quartile ----------------------------------------------------------

function applyTopQuartile(
  traces: readonly ProductionTrace[],
  rule: TopQuartileRule,
): readonly ProductionTrace[] {
  // Extract numeric score for each trace; drop those missing the field.
  const scored: Array<{ t: ProductionTrace; s: number }> = [];
  for (const t of traces) {
    const raw = resolveJsonPath(t, rule.by);
    if (typeof raw === "number" && Number.isFinite(raw)) {
      scored.push({ t, s: raw });
    }
  }
  if (scored.length === 0) return [];
  // Sort descending by score. Tie-breaker: original input order (stable).
  const indexed = scored.map((e, i) => ({ ...e, i }));
  indexed.sort((a, b) => (b.s - a.s) || (a.i - b.i));
  // Take top N% — inclusive, round up so the cutoff is generous for small sets.
  const fraction = (100 - rule.percentile) / 100;
  // `percentile: 75` means "top 25%" in spec: keep scored.length * 0.25 items.
  // We invert: keep (100 - percentile)% of the list.
  const n = Math.max(0, Math.ceil(indexed.length * fraction));
  return indexed.slice(0, n).map((e) => e.t);
}

// ---- Contrastive -----------------------------------------------------------

/**
 * Pair failure traces with success traces within the same cluster. Pairs are
 * emitted as `[failure, success]` tuples. The resulting rows[] is the set of
 * traces that participated in at least one pair (deduplicated, preserving
 * the order in which traces first appeared in a pair).
 *
 * `maxPairsPerCluster` bounds the number of pairs per cluster; traces beyond
 * that cap are not included.
 */
function applyContrastive(
  traces: readonly ProductionTrace[],
  rule: ContrastiveRule,
  clusterKey: (t: ProductionTrace) => string,
): { readonly rows: readonly ProductionTrace[]; readonly pairs: readonly TracePair[] } {
  const failures: Map<string, ProductionTrace[]> = new Map();
  const successes: Map<string, ProductionTrace[]> = new Map();
  for (const t of traces) {
    if (matchExpression(t, rule.failureCriterion as MatchExpression)) {
      const k = clusterKey(t);
      const b = failures.get(k);
      if (b === undefined) failures.set(k, [t]);
      else b.push(t);
    } else if (matchExpression(t, rule.successCriterion as MatchExpression)) {
      const k = clusterKey(t);
      const b = successes.get(k);
      if (b === undefined) successes.set(k, [t]);
      else b.push(t);
    }
  }
  const cap = rule.maxPairsPerCluster ?? Number.POSITIVE_INFINITY;
  const pairs: TracePair[] = [];
  const seenRowIds = new Set<string>();
  const rows: ProductionTrace[] = [];
  const pushRow = (t: ProductionTrace) => {
    if (seenRowIds.has(t.traceId)) return;
    seenRowIds.add(t.traceId);
    rows.push(t);
  };
  // Iterate clusters deterministically (Map preserves insertion order).
  for (const [k, fList] of failures) {
    const sList = successes.get(k);
    if (sList === undefined || sList.length === 0) continue;
    const pairCount = Math.min(fList.length, sList.length, cap);
    for (let i = 0; i < pairCount; i += 1) {
      const f = fList[i];
      const s = sList[i];
      pairs.push([f, s]);
      pushRow(f);
      pushRow(s);
    }
  }
  return { rows, pairs };
}

// ---- Split rule extraction -------------------------------------------------

export function extractSplitRule(rules: readonly SelectionRule[]): SplitRule | null {
  // Last split rule wins if multiple are specified.
  let found: SplitRule | null = null;
  for (const r of rules) {
    if (r.type === "split") found = r;
  }
  return found;
}

export function rulesWithoutSplit(rules: readonly SelectionRule[]): SelectionRule[] {
  return rules.filter((r) => r.type !== "split");
}

// ---- Default cluster-key inference (fallback for flat mode) ----------------

function inferClusterKey(t: ProductionTrace): string {
  return t.env.taskType ?? "uncategorized";
}

// Re-export for orchestrator & tests.
export { seededShuffle };
