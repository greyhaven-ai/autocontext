/**
 * Trace clustering strategies for dataset generation (spec §8.1).
 *
 *   Tier 1 — `clusterByTaskType`:   group by `env.taskType`. Zero compute.
 *   Tier 2 — `clusterByRules`:      first-matching-rule wins over a JSON-path
 *                                   + operator matcher hand-rolled to avoid
 *                                   pulling a dependency.
 *
 * Both functions preserve input order within each cluster (stable output
 * ordering — same traces fed in the same order always produce the same cluster
 * grouping). The returned Map's insertion order is the order in which a cluster
 * first received a trace; callers that need lexicographic cluster ordering can
 * sort the Map's keys themselves.
 *
 * Tier 3 (embedding clustering) is explicitly out of scope for OSS per spec —
 * customers needing it populate `env.taskType` via their own embedder,
 * reducing the problem to Tier 1.
 */
import type { ProductionTrace } from "../contract/types.js";
import type { ClusterConfig, MatchExpression, MatchOperator } from "./types.js";

/** Uncategorized bucket name for traces without `env.taskType`. */
export const UNCATEGORIZED_CLUSTER = "uncategorized";

/**
 * Tier 1 clustering: group traces by `env.taskType`. Traces with no taskType
 * (or an empty string taskType) go to the `uncategorized` bucket.
 */
export function clusterByTaskType(
  traces: readonly ProductionTrace[],
): Map<string, ProductionTrace[]> {
  const out = new Map<string, ProductionTrace[]>();
  for (const trace of traces) {
    const key = trace.env.taskType !== undefined && trace.env.taskType.length > 0
      ? trace.env.taskType
      : UNCATEGORIZED_CLUSTER;
    const bucket = out.get(key);
    if (bucket === undefined) {
      out.set(key, [trace]);
    } else {
      bucket.push(trace);
    }
  }
  return out;
}

/**
 * Tier 2 clustering: rule-based. First matching rule wins. A rule with
 * `match: { default: true }` (as a single-key MatchExpression with the
 * `default` operator) acts as the catch-all.
 *
 * If no rule matches and no catch-all is present, the trace is assigned to
 * the {@link UNCATEGORIZED_CLUSTER} bucket. Callers concerned about silent
 * drop-through should include an explicit `default: true` rule.
 */
export function clusterByRules(
  traces: readonly ProductionTrace[],
  config: ClusterConfig,
): Map<string, ProductionTrace[]> {
  const out = new Map<string, ProductionTrace[]>();
  for (const trace of traces) {
    let assigned: string | null = null;
    for (const rule of config.rules) {
      if (matchExpression(trace, rule.match)) {
        assigned = rule.id;
        break;
      }
    }
    const key = assigned ?? UNCATEGORIZED_CLUSTER;
    const bucket = out.get(key);
    if (bucket === undefined) {
      out.set(key, [trace]);
    } else {
      bucket.push(trace);
    }
  }
  return out;
}

// ---- Small JSON-path + operator matcher ------------------------------------

/**
 * A MatchExpression is a map of JSON-path → operator. All path/operator pairs
 * must match for the expression to succeed (AND semantics). An empty
 * expression never matches (would be trivially true; treated as a config
 * error and returns false).
 *
 * Supported operators:
 *   - `equals`  — deep JSON equality
 *   - `contains`— string: substring; string[]: ANY-match
 *   - `default` — a trivially-true marker (used for catch-all rules).
 *                 Ignores the path entirely.
 */
export function matchExpression(
  trace: ProductionTrace,
  expr: MatchExpression,
): boolean {
  const entries = Object.entries(expr);
  if (entries.length === 0) return false;
  for (const [path, op] of entries) {
    if (!matchOperator(trace, path, op)) return false;
  }
  return true;
}

function matchOperator(trace: ProductionTrace, path: string, op: MatchOperator): boolean {
  if (op.default === true) return true;

  const value = resolveJsonPath(trace, path);

  if (op.equals !== undefined) {
    if (!deepEqual(value, op.equals)) return false;
  }
  if (op.contains !== undefined) {
    if (!containsMatch(value, op.contains)) return false;
  }
  // If no operator was specified and `default` was absent, no match.
  if (op.equals === undefined && op.contains === undefined) return false;
  return true;
}

function containsMatch(value: unknown, needle: string | readonly string[]): boolean {
  if (typeof needle === "string") {
    return containsScalar(value, needle);
  }
  return needle.some((n) => containsScalar(value, n));
}

function containsScalar(value: unknown, needle: string): boolean {
  if (typeof value === "string") return value.includes(needle);
  if (Array.isArray(value)) {
    return value.some((item) => item === needle || (typeof item === "string" && item.includes(needle)));
  }
  return false;
}

/**
 * Minimal JSON-path resolver supporting dotted keys and bracketed integer
 * indices: `messages[0].content`, `toolCalls[0].toolName`, `env.taskType`.
 *
 * Returns `undefined` for missing keys/out-of-range indices.
 */
export function resolveJsonPath(root: unknown, path: string): unknown {
  const tokens = tokenizePath(path);
  if (tokens === null) return undefined;
  let current: unknown = root;
  for (const tok of tokens) {
    if (current === null || current === undefined) return undefined;
    if (typeof tok === "number") {
      if (!Array.isArray(current)) return undefined;
      if (tok < 0 || tok >= current.length) return undefined;
      current = current[tok];
      continue;
    }
    if (Array.isArray(current)) return undefined;
    if (typeof current !== "object") return undefined;
    const rec = current as Record<string, unknown>;
    if (!Object.prototype.hasOwnProperty.call(rec, tok)) return undefined;
    current = rec[tok];
  }
  return current;
}

function tokenizePath(path: string): Array<string | number> | null {
  // Accept sequences like `a.b[0].c` or `a[0][1].b`.
  const tokens: Array<string | number> = [];
  let i = 0;
  let buf = "";
  const flush = () => {
    if (buf.length > 0) {
      tokens.push(buf);
      buf = "";
    }
  };
  while (i < path.length) {
    const c = path[i];
    if (c === ".") {
      flush();
      i += 1;
      continue;
    }
    if (c === "[") {
      flush();
      const close = path.indexOf("]", i);
      if (close === -1) return null;
      const idxStr = path.slice(i + 1, close);
      if (!/^(0|[1-9][0-9]*)$/.test(idxStr)) return null;
      tokens.push(Number(idxStr));
      i = close + 1;
      continue;
    }
    buf += c;
    i += 1;
  }
  flush();
  return tokens;
}

function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (a === null || b === null) return false;
  if (typeof a !== typeof b) return false;
  if (typeof a !== "object") return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  if (Array.isArray(a) && Array.isArray(b)) {
    if (a.length !== b.length) return false;
    return a.every((v, i) => deepEqual(v, b[i]));
  }
  const ao = a as Record<string, unknown>;
  const bo = b as Record<string, unknown>;
  const ak = Object.keys(ao).sort();
  const bk = Object.keys(bo).sort();
  if (ak.length !== bk.length) return false;
  return ak.every((k, i) => k === bk[i] && deepEqual(ao[k], bo[k]));
}
