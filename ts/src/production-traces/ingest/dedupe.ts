import { createReadStream, existsSync, appendFileSync, mkdirSync, readdirSync, statSync } from "node:fs";
import { createInterface } from "node:readline";
import { dirname, join } from "node:path";
import {
  parseProductionTraceId,
  type ProductionTraceId,
} from "../contract/branded-ids.js";
import { productionTracesRoot, seenIdsPath } from "./paths.js";

/**
 * Append-only trace-id dedupe cache. Format is one ULID per line under
 * `<cwd>/.autocontext/production-traces/seen-ids.jsonl`. We intentionally
 * store bare IDs rather than JSON records — the file is read far more often
 * than written, and streaming becomes simpler.
 *
 * `loadSeenIds` reads the file via a streaming reader and is safe for files
 * with tens of thousands of entries. Malformed / non-ULID lines are silently
 * skipped (recovery-friendly); callers that need strictness should pair with
 * `rebuildSeenIdsFromIngested` and write a fresh cache.
 */

export async function loadSeenIds(cwd: string): Promise<Set<ProductionTraceId>> {
  const path = seenIdsPath(cwd);
  const out = new Set<ProductionTraceId>();
  if (!existsSync(path)) return out;

  const stream = createReadStream(path, { encoding: "utf-8" });
  const rl = createInterface({ input: stream, crlfDelay: Infinity });
  for await (const raw of rl) {
    const line = raw.trim();
    if (line.length === 0) continue;
    const id = parseProductionTraceId(line);
    if (id !== null) out.add(id);
  }
  return out;
}

export async function appendSeenId(cwd: string, traceId: ProductionTraceId): Promise<void> {
  const path = seenIdsPath(cwd);
  mkdirSync(dirname(path), { recursive: true });
  appendFileSync(path, `${traceId}\n`, "utf-8");
}

/**
 * Recovery helper — walks `<root>/ingested/<date>/*.jsonl`, reads each JSON
 * line's `traceId`, and returns the combined set. Use when `seen-ids.jsonl`
 * is lost or suspected-corrupt. Malformed lines are skipped.
 */
export async function rebuildSeenIdsFromIngested(cwd: string): Promise<Set<ProductionTraceId>> {
  const out = new Set<ProductionTraceId>();
  const ingestedRoot = join(productionTracesRoot(cwd), "ingested");
  if (!existsSync(ingestedRoot)) return out;

  const dateEntries = readdirSync(ingestedRoot);
  for (const dateDir of dateEntries) {
    const fullDate = join(ingestedRoot, dateDir);
    if (!statSync(fullDate).isDirectory()) continue;
    const files = readdirSync(fullDate);
    for (const file of files) {
      if (!file.endsWith(".jsonl")) continue;
      const full = join(fullDate, file);
      const stream = createReadStream(full, { encoding: "utf-8" });
      const rl = createInterface({ input: stream, crlfDelay: Infinity });
      for await (const raw of rl) {
        const line = raw.trim();
        if (line.length === 0) continue;
        let parsed: unknown;
        try {
          parsed = JSON.parse(line);
        } catch {
          continue;
        }
        if (parsed === null || typeof parsed !== "object") continue;
        const candidate = (parsed as { traceId?: unknown }).traceId;
        if (typeof candidate !== "string") continue;
        const id = parseProductionTraceId(candidate);
        if (id !== null) out.add(id);
      }
    }
  }
  return out;
}
