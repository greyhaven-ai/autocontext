import {
  createReadStream,
  existsSync,
  mkdirSync,
  readdirSync,
  renameSync,
  statSync,
  writeFileSync,
  unlinkSync,
} from "node:fs";
import { createInterface } from "node:readline";
import { join } from "node:path";
import { acquireLock } from "./lock.js";
import {
  incomingDir,
  ingestedDir,
  failedDir,
  productionTracesRoot,
} from "./paths.js";
import { loadSeenIds, appendSeenId } from "./dedupe.js";
import { validateIngestedLine } from "./validator.js";
import { markRedactions } from "./redaction-phase.js";
import { writeReceipt, writeErrorFile, type PerLineError } from "./receipt.js";
import type { ProductionTrace } from "../contract/types.js";
import type { ProductionTraceId } from "../contract/branded-ids.js";
import { PRODUCTION_TRACE_SCHEMA_VERSION } from "../contract/types.js";

export interface IngestOpts {
  /** ISO timestamp; skip batches whose file mtime is before this. */
  readonly since?: string;
  /** Strict mode — any rejected line fails the whole batch (no partial success). */
  readonly strict?: boolean;
  /** Dry-run — validate but don't move files, update seen-ids, or take the lock's side-effects. */
  readonly dryRun?: boolean;
}

export interface IngestReport {
  readonly batchesProcessed: number;
  /** Batches with ≥1 valid ingested trace after processing. */
  readonly batchesSucceeded: number;
  /** Batches where zero lines produced a successful ingestion. */
  readonly batchesFailedEntirely: number;
  readonly tracesIngested: number;
  readonly duplicatesSkipped: number;
  readonly linesRejected: number;
}

interface BatchFileInfo {
  readonly date: string;
  readonly batchId: string;
  readonly path: string;
  readonly mtimeMs: number;
}

/**
 * Main ingestion orchestrator — see spec §6.3.
 *
 * Contract:
 *   1. Acquire `.autocontext/lock` (shared with Foundation B registry).
 *   2. Load `seen-ids.jsonl` into memory.
 *   3. Walk `incoming/<date>/*.jsonl`, filtered by `--since`.
 *   4. For each batch:
 *       - Read line-by-line, validate, invoke `markRedactions` (passthrough in L3).
 *       - Skip duplicates; track per-line failures.
 *       - strict + any-failure → batch moves to `failed/`, zero ingestions.
 *       - else → successful lines written to `ingested/<date>/<batch>.jsonl`,
 *         `receipt.json` written; if any line failed, `error.json` is also
 *         written; `seen-ids.jsonl` extended.
 *   5. Release lock; return report.
 *
 * `dry-run` skips all file moves and seen-ids updates but still reports what
 * would have happened.
 */
export async function ingestBatches(
  cwd: string,
  opts: IngestOpts,
): Promise<IngestReport> {
  const strict = opts.strict ?? false;
  const dryRun = opts.dryRun ?? false;
  const sinceMs = opts.since !== undefined ? Date.parse(opts.since) : undefined;
  if (sinceMs !== undefined && Number.isNaN(sinceMs)) {
    throw new Error(`ingestBatches: --since '${opts.since}' is not a parseable timestamp`);
  }

  // Acquire the shared flock. Skip during dry-run so concurrent dry-run calls
  // can coexist — matches spec §6.3 ("--dry-run: validate and detect without
  // moving files or updating seen-ids") taken at face value.
  const lock = dryRun ? null : acquireLock(cwd);
  try {
    const seen = await loadSeenIds(cwd);

    const batches = enumerateBatches(cwd, sinceMs);

    let batchesProcessed = 0;
    let batchesSucceeded = 0;
    let batchesFailedEntirely = 0;
    let tracesIngested = 0;
    let duplicatesSkipped = 0;
    let linesRejected = 0;

    for (const batch of batches) {
      batchesProcessed += 1;
      const outcome = await processBatch(batch, seen);
      linesRejected += outcome.errors.length;

      // Strict mode: any per-line failure discards the whole batch, even if
      // other lines validated successfully. Their traceIds are NOT added to
      // seen-ids because nothing gets written to ingested/.
      const strictReject = strict && outcome.errors.length > 0;

      if (strictReject) {
        duplicatesSkipped += outcome.duplicates;
        batchesFailedEntirely += 1;
        if (!dryRun) {
          await moveToFailed(cwd, batch, outcome.errors);
        }
        continue;
      }

      tracesIngested += outcome.successes.length;
      duplicatesSkipped += outcome.duplicates;

      if (outcome.successes.length === 0) {
        batchesFailedEntirely += 1;
      } else {
        batchesSucceeded += 1;
      }

      if (dryRun) continue;

      await moveToIngested(cwd, batch, outcome);
      for (const s of outcome.successes) {
        await appendSeenId(cwd, s.traceId);
        seen.add(s.traceId);
      }
    }

    return {
      batchesProcessed,
      batchesSucceeded,
      batchesFailedEntirely,
      tracesIngested,
      duplicatesSkipped,
      linesRejected,
    };
  } finally {
    lock?.release();
  }
}

interface BatchOutcome {
  readonly successes: readonly ProductionTrace[];
  readonly duplicates: number;
  readonly errors: readonly PerLineError[];
}

async function processBatch(
  batch: BatchFileInfo,
  seen: Set<ProductionTraceId>,
): Promise<BatchOutcome> {
  const successes: ProductionTrace[] = [];
  const errors: PerLineError[] = [];
  let duplicates = 0;

  const stream = createReadStream(batch.path, { encoding: "utf-8" });
  const rl = createInterface({ input: stream, crlfDelay: Infinity });
  let lineNo = 0;
  for await (const rawLine of rl) {
    lineNo += 1;
    const line = rawLine;
    if (line.trim().length === 0) continue;

    const r = validateIngestedLine(line);
    if (!r.ok) {
      errors.push({
        lineNo,
        reasons: [r.reason],
        ...(r.attemptedTraceId !== undefined ? { attemptedTraceId: r.attemptedTraceId } : {}),
      });
      continue;
    }

    // Mark-at-ingest (Layer 3 passthrough).
    const marked = markRedactions(r.trace);

    if (seen.has(marked.traceId)) {
      duplicates += 1;
      continue;
    }
    successes.push(marked);
  }

  return { successes, duplicates, errors };
}

function enumerateBatches(cwd: string, sinceMs: number | undefined): BatchFileInfo[] {
  const root = join(productionTracesRoot(cwd), "incoming");
  if (!existsSync(root)) return [];
  const out: BatchFileInfo[] = [];
  for (const dateEntry of readdirSync(root)) {
    const dateDir = join(root, dateEntry);
    const st = statSync(dateDir);
    if (!st.isDirectory()) continue;
    for (const fileEntry of readdirSync(dateDir)) {
      if (!fileEntry.endsWith(".jsonl")) continue;
      const full = join(dateDir, fileEntry);
      const fst = statSync(full);
      if (sinceMs !== undefined && fst.mtimeMs < sinceMs) continue;
      out.push({
        date: dateEntry,
        batchId: fileEntry.slice(0, -".jsonl".length),
        path: full,
        mtimeMs: fst.mtimeMs,
      });
    }
  }
  // Deterministic order — sort by date then batchId.
  out.sort((a, b) => {
    if (a.date !== b.date) return a.date < b.date ? -1 : 1;
    if (a.batchId !== b.batchId) return a.batchId < b.batchId ? -1 : 1;
    return 0;
  });
  return out;
}

async function moveToIngested(
  cwd: string,
  batch: BatchFileInfo,
  outcome: BatchOutcome,
): Promise<void> {
  const destDir = ingestedDir(cwd, batch.date);
  mkdirSync(destDir, { recursive: true });
  const destJsonl = join(destDir, `${batch.batchId}.jsonl`);
  const body =
    outcome.successes.length === 0
      ? ""
      : outcome.successes.map((t) => JSON.stringify(t)).join("\n") + "\n";
  writeFileSync(destJsonl, body, "utf-8");

  writeReceipt(join(destDir, `${batch.batchId}.receipt.json`), {
    count: outcome.successes.length + outcome.duplicates + outcome.errors.length,
    tracesIngested: outcome.successes.length,
    duplicatesSkipped: outcome.duplicates,
    ingestedAt: new Date().toISOString(),
    schemaVersion: PRODUCTION_TRACE_SCHEMA_VERSION,
  });

  if (outcome.errors.length > 0) {
    writeErrorFile(join(destDir, `${batch.batchId}.error.json`), {
      perLineErrors: outcome.errors,
    });
  }

  // Remove the source batch from incoming/ now that we've written the
  // canonical copy to ingested/. Using renameSync would be nicer (atomic)
  // but the destination already has the filtered content, so unlink is the
  // correct semantic here.
  unlinkSync(batch.path);
}

async function moveToFailed(
  cwd: string,
  batch: BatchFileInfo,
  errors: readonly PerLineError[],
): Promise<void> {
  const destDir = failedDir(cwd, batch.date);
  mkdirSync(destDir, { recursive: true });
  const destJsonl = join(destDir, `${batch.batchId}.jsonl`);
  // Atomic move preserves the original bytes — operators can re-drop after
  // fixing the upstream emitter.
  renameSync(batch.path, destJsonl);

  writeErrorFile(join(destDir, `${batch.batchId}.error.json`), {
    perLineErrors: errors,
  });
}
