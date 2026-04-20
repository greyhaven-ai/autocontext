import { join } from "node:path";

/**
 * Path helpers for the on-disk production-traces layout. All paths are
 * derived from a `cwd` (the autocontext working directory) and — for
 * partitioned directories — a `YYYY-MM-DD` date string in UTC.
 *
 * See spec §6.1 for the full directory contract.
 */

const ROOT = ".autocontext";
const PRODUCTION_TRACES = "production-traces";

export function productionTracesRoot(cwd: string): string {
  return join(cwd, ROOT, PRODUCTION_TRACES);
}

export function incomingDir(cwd: string, date?: string): string {
  return join(productionTracesRoot(cwd), "incoming", date ?? todayUtc());
}

export function ingestedDir(cwd: string, date?: string): string {
  return join(productionTracesRoot(cwd), "ingested", date ?? todayUtc());
}

export function failedDir(cwd: string, date?: string): string {
  return join(productionTracesRoot(cwd), "failed", date ?? todayUtc());
}

export function seenIdsPath(cwd: string): string {
  return join(productionTracesRoot(cwd), "seen-ids.jsonl");
}

export function gcLogPath(cwd: string): string {
  return join(productionTracesRoot(cwd), "gc-log.jsonl");
}

/**
 * Extract the UTC date portion of an ISO-8601 timestamp. Used to compute the
 * date-partition directory for a given trace. Throws if the input cannot be
 * parsed as a date.
 */
export function dateOf(isoTimestamp: string): string {
  const ms = Date.parse(isoTimestamp);
  if (Number.isNaN(ms)) {
    throw new Error(`dateOf: cannot parse '${isoTimestamp}' as a date`);
  }
  return new Date(ms).toISOString().slice(0, 10);
}

function todayUtc(): string {
  return new Date().toISOString().slice(0, 10);
}
