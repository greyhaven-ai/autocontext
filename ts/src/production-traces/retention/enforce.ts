// Core retention operation — extracted from Layer 7's `cli/prune.ts`.
//
// `enforceRetention` walks `.autocontext/production-traces/ingested/<YYYY-MM-DD>/*.jsonl`
// and, per the loaded `RetentionPolicy` (spec §6.6), deletes traces whose
// `timing.endedAt` is older than `retentionDays` AND whose `outcome.label` is
// NOT in `preserveCategories`. Each deletion is logged to
// `.autocontext/production-traces/gc-log.jsonl` via `appendGcLogEntry`.
//
// DDD vocabulary (verbatim from spec §6.6):
//   - evaluated / deleted / preserved / tooYoung counters in RetentionReport
//   - batchesAffected: list of batch files rewritten or flagged-for-rewrite
//   - gcLogEntriesAppended: audit line count
//
// Determinism: callers pass `nowUtc` explicitly. The function NEVER calls
// `Date.now()` or `new Date()` internally so tests can replay 100-day
// time-travel scenarios byte-deterministically (cf. spec §10.3 integration
// flow 4). `batchesAffected` and `GcLogEntry.batchPath` hold paths RELATIVE
// to `cwd` so identical logical fixtures produce identical reports even when
// mounted at different absolute locations.
//
// Batching semantics (`gcBatchSize`): the retention phase may inspect more
// than `gcBatchSize` traces, but it MUST NOT delete more than that number in
// a single run. Eligible traces beyond the cap remain on disk and become
// candidates for the next enforcement run (large backlogs drain over multiple
// runs; latency per ingest is bounded).

import {
  existsSync,
  readFileSync,
  readdirSync,
  statSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { join, relative } from "node:path";
import type { ProductionTrace } from "../contract/types.js";
import { productionTracesRoot } from "../ingest/paths.js";
import { appendGcLogEntry } from "./gc-log.js";
import type { LoadedRetentionPolicy } from "./policy.js";

export type GcLogEntry = {
  readonly traceId: string;
  readonly batchPath: string;
  readonly deletedAt: string;
  readonly reason: "retention-expired";
};

export type RetentionInputs = {
  /** Project root that holds `.autocontext/production-traces/`. */
  readonly cwd: string;
  /** Loaded policy; callers obtain via `loadRetentionPolicy(cwd)`. */
  readonly policy: LoadedRetentionPolicy;
  /** Wall-clock timestamp used as the retention reference point. */
  readonly nowUtc: Date;
  /** When true, classify deletions without touching any files. */
  readonly dryRun: boolean;
};

export type RetentionReport = {
  /** Total traces inspected across all ingested/ batches this run. */
  readonly evaluated: number;
  /** Traces physically removed (always 0 in dry-run). */
  readonly deleted: number;
  /** Traces retained because outcome.label is in preserveCategories. */
  readonly preserved: number;
  /** Traces retained because endedAt is newer than the retention threshold. */
  readonly tooYoung: number;
  /** Batch files whose contents changed (paths relative to `cwd`). */
  readonly batchesAffected: readonly string[];
  /** Number of gc-log.jsonl lines appended (0 in dry-run). */
  readonly gcLogEntriesAppended: number;
};

const MS_PER_DAY = 24 * 60 * 60 * 1000;
const EMPTY_REPORT: RetentionReport = {
  evaluated: 0,
  deleted: 0,
  preserved: 0,
  tooYoung: 0,
  batchesAffected: [],
  gcLogEntriesAppended: 0,
};

export async function enforceRetention(inputs: RetentionInputs): Promise<RetentionReport> {
  const { cwd, policy, nowUtc, dryRun } = inputs;

  // preserveAll is the compliance-bound escape hatch — short-circuit before
  // any filesystem work.
  if (policy.preserveAll) {
    return EMPTY_REPORT;
  }

  const root = join(productionTracesRoot(cwd), "ingested");
  if (!existsSync(root)) {
    return EMPTY_REPORT;
  }

  const thresholdMs = nowUtc.getTime() - policy.retentionDays * MS_PER_DAY;
  const preserveSet = new Set<string>(policy.preserveCategories);

  let evaluated = 0;
  let deleted = 0;
  let preserved = 0;
  let tooYoung = 0;
  let gcLogEntriesAppended = 0;
  const batchesAffected: string[] = [];
  let budgetRemaining = policy.gcBatchSize;

  // Deterministic ordering: date dir ascending, then batch file ascending.
  for (const date of readdirSync(root).sort()) {
    const dateDir = join(root, date);
    if (!statSync(dateDir).isDirectory()) continue;

    for (const file of readdirSync(dateDir).sort()) {
      if (!file.endsWith(".jsonl")) continue;
      const path = join(dateDir, file);
      const relPath = relative(cwd, path);

      const text = readFileSync(path, "utf-8");
      const lines = text.split("\n");
      const keep: string[] = [];
      const deletedTraceIds: string[] = [];

      for (const rawLine of lines) {
        if (rawLine.length === 0) continue;
        if (rawLine.trim().length === 0) {
          keep.push(rawLine);
          continue;
        }
        // Malformed line: preserve so a later corrective ingest can re-process.
        // Not counted as "evaluated" (we don't know its age or label).
        let parsed: ProductionTrace;
        try {
          parsed = JSON.parse(rawLine) as ProductionTrace;
        } catch {
          keep.push(rawLine);
          continue;
        }

        evaluated += 1;
        const endedMs = Date.parse(parsed.timing.endedAt);
        if (Number.isNaN(endedMs) || endedMs > thresholdMs) {
          tooYoung += 1;
          keep.push(rawLine);
          continue;
        }
        const label = parsed.outcome?.label;
        if (label !== undefined && preserveSet.has(label)) {
          preserved += 1;
          keep.push(rawLine);
          continue;
        }
        if (budgetRemaining <= 0) {
          // Exhausted gcBatchSize — keep the trace for the next run. This
          // is the bounded-latency guarantee per spec §6.6.
          keep.push(rawLine);
          continue;
        }
        // Eligible for deletion.
        deletedTraceIds.push(parsed.traceId);
        budgetRemaining -= 1;
        if (!dryRun) {
          deleted += 1;
        }
      }

      // Did anything in this batch change?
      if (deletedTraceIds.length === 0) continue;
      batchesAffected.push(relPath);

      if (dryRun) continue;

      // Emit gc-log entries for each deletion (single append per entry).
      for (const traceId of deletedTraceIds) {
        appendGcLogEntry(cwd, {
          traceId,
          batchPath: relPath,
          deletedAt: nowUtc.toISOString(),
          reason: "retention-expired",
        });
        gcLogEntriesAppended += 1;
      }

      // Rewrite the batch file with only the kept lines, or remove it if
      // nothing remains.
      const keptNonEmpty = keep.filter((l) => l.trim().length > 0);
      if (keptNonEmpty.length === 0) {
        try {
          unlinkSync(path);
        } catch {
          // File may have been removed concurrently; ignore.
        }
      } else {
        writeFileSync(path, keptNonEmpty.join("\n") + "\n", "utf-8");
      }
    }
  }

  return {
    evaluated,
    deleted,
    preserved,
    tooYoung,
    batchesAffected,
    gcLogEntriesAppended,
  };
}
