// Local-view helpers for reading ingested traces from disk.
//
// Used by `list`, `show`, `stats`, `build-dataset`, `export`, and `prune`.
// Reads `.autocontext/production-traces/ingested/<date>/<batch>.jsonl` files
// and returns parsed `ProductionTrace` objects.
//
// Local-view discipline (spec §7.5): NO redaction is applied by this loader.
// The caller decides whether to run `applyRedactions` at its own export
// boundary. Tests rely on this invariant — do not change it without updating
// the list/show/stats spec table.

import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import type { ProductionTrace } from "../../contract/types.js";
import { productionTracesRoot } from "../../ingest/paths.js";

export interface TraceFilter {
  readonly since?: string;
  readonly until?: string;
  readonly env?: string;
  readonly app?: string;
  readonly provider?: string;
  readonly outcome?: string;
  readonly limit?: number;
}

/**
 * Read every trace in `.autocontext/production-traces/ingested/<date>/*.jsonl`.
 * Files that fail to parse are skipped (a corrupt trace file should not kill
 * the whole command — see ingest layer for strict validation at write time).
 *
 * Returns traces in stable order: sorted by (date, batchId, line-number), so
 * two reads of the same on-disk state return byte-identical results (used by
 * the stats-idempotence test).
 */
export function loadIngestedTraces(
  cwd: string,
  filter: TraceFilter = {},
): ProductionTrace[] {
  const root = join(productionTracesRoot(cwd), "ingested");
  if (!existsSync(root)) return [];

  const sinceMs = parseTimeFlag("since", filter.since);
  const untilMs = parseTimeFlag("until", filter.until);

  const dates = readdirSync(root)
    .filter((d) => statSync(join(root, d)).isDirectory())
    .sort();
  const out: ProductionTrace[] = [];
  for (const date of dates) {
    const dateDir = join(root, date);
    const files = readdirSync(dateDir)
      .filter((f) => f.endsWith(".jsonl"))
      .sort();
    for (const file of files) {
      const path = join(dateDir, file);
      const text = readFileSync(path, "utf-8");
      for (const rawLine of text.split("\n")) {
        if (rawLine.trim().length === 0) continue;
        let parsed: unknown;
        try {
          parsed = JSON.parse(rawLine);
        } catch {
          continue;
        }
        if (!isTrace(parsed)) continue;
        if (!matchesFilter(parsed, filter, sinceMs, untilMs)) continue;
        out.push(parsed);
        if (filter.limit !== undefined && out.length >= filter.limit) {
          return out;
        }
      }
    }
  }
  return out;
}

/**
 * Locate a trace by its ID. O(n) — we don't maintain a disk index. Acceptable
 * because Foundation A is designed for local operator workflows on bounded
 * stores; hosted-scale lookups are Layer 8+ with a SQLite index.
 */
export function findTraceById(
  cwd: string,
  traceId: string,
): ProductionTrace | null {
  const traces = loadIngestedTraces(cwd);
  return traces.find((t) => t.traceId === traceId) ?? null;
}

function parseTimeFlag(name: string, value: string | undefined): number | undefined {
  if (value === undefined) return undefined;
  const ms = Date.parse(value);
  if (Number.isNaN(ms)) {
    throw new Error(`--${name} '${value}' is not a parseable ISO timestamp`);
  }
  return ms;
}

function isTrace(v: unknown): v is ProductionTrace {
  if (v === null || typeof v !== "object") return false;
  const r = v as Record<string, unknown>;
  return (
    typeof r.traceId === "string" &&
    typeof r.schemaVersion === "string" &&
    typeof r.env === "object" &&
    r.env !== null
  );
}

function matchesFilter(
  trace: ProductionTrace,
  filter: TraceFilter,
  sinceMs: number | undefined,
  untilMs: number | undefined,
): boolean {
  if (sinceMs !== undefined) {
    const startedMs = Date.parse(trace.timing.startedAt);
    if (Number.isNaN(startedMs) || startedMs < sinceMs) return false;
  }
  if (untilMs !== undefined) {
    const endedMs = Date.parse(trace.timing.endedAt);
    if (Number.isNaN(endedMs) || endedMs > untilMs) return false;
  }
  if (filter.env !== undefined && trace.env.environmentTag !== filter.env) return false;
  if (filter.app !== undefined && trace.env.appId !== filter.app) return false;
  if (filter.provider !== undefined && trace.provider.name !== filter.provider) return false;
  if (filter.outcome !== undefined) {
    const label = trace.outcome?.label;
    if (label !== filter.outcome) return false;
  }
  return true;
}
