// `autoctx production-traces prune [--dry-run]`
//
// Minimum viable retention enforcement for Layer 7 (spec §6.6). Walks
// `ingested/<date>/` and deletes traces older than `retentionDays` whose
// `outcome.label` is NOT in `preserveCategories`. Logs each deletion to
// `gc-log.jsonl`.
//
// LAYERING NOTE: Layer 8 is expected to own retention properly (with its own
// module + JSON schema + ingest-phase-2 hook). This Layer 7 implementation is
// deliberately minimal: it reads the retention policy, applies it out-of-band,
// and emits the same on-disk artifacts (gc-log.jsonl) that the Layer 8
// version will continue producing. When Layer 8 lands, this file should
// shrink to a thin wrapper around `retention/prune.ts`.

import {
  appendFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  statSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { join } from "node:path";
import type { ProductionTrace } from "../contract/types.js";
import { productionTracesRoot, gcLogPath } from "../ingest/paths.js";
import { acquireLock } from "../ingest/lock.js";
import {
  loadRetentionPolicy,
  type RetentionPolicy,
} from "./_shared/retention-policy.js";
import { EXIT } from "./_shared/exit-codes.js";
import { formatOutput, type OutputMode } from "./_shared/output-formatters.js";
import { parseFlags, stringFlag, booleanFlag } from "./_shared/flags.js";
import type { CliContext, CliResult } from "./_shared/types.js";

export const PRUNE_HELP_TEXT = `autoctx production-traces prune — enforce retention policy out-of-band

Usage:
  autoctx production-traces prune [--dry-run] [--output json|pretty|table]

Behavior:
  Loads retention-policy.json (defaults to 90-day retention if missing).
  Walks ingested/<date>/*.jsonl; for each trace older than retentionDays
  whose outcome.label is NOT in preserveCategories, queues for deletion.
  With --dry-run: prints what would be deleted, no changes.
  Without --dry-run: deletes + appends to gc-log.jsonl.
  preserveAll: true short-circuits with zero deletions.

Acquires .autocontext/lock (shared with Foundation B) for the whole run.
`;

interface PruneReport {
  readonly dryRun: boolean;
  readonly retentionDays: number;
  readonly scannedFiles: number;
  readonly scannedTraces: number;
  readonly deletedTraces: number;
  readonly preservedByCategory: number;
  readonly preservedByAge: number;
  readonly preserveAll: boolean;
}

export async function runPrune(
  args: readonly string[],
  ctx: CliContext,
): Promise<CliResult> {
  if (args[0] === "--help" || args[0] === "-h") {
    return { stdout: PRUNE_HELP_TEXT, stderr: "", exitCode: EXIT.SUCCESS };
  }
  const flags = parseFlags(args, {
    "dry-run": { type: "boolean" },
    output: { type: "string", default: "pretty" },
  });
  if ("error" in flags) {
    return { stdout: "", stderr: flags.error, exitCode: EXIT.DOMAIN_FAILURE };
  }
  const dryRun = booleanFlag(flags.value, "dry-run");
  const output = (stringFlag(flags.value, "output") ?? "pretty") as OutputMode;

  let policy: RetentionPolicy;
  try {
    policy = await loadRetentionPolicy(ctx.cwd);
  } catch (err) {
    return { stdout: "", stderr: `prune: ${msgOf(err)}`, exitCode: EXIT.INVALID_CONFIG };
  }

  let lock;
  try {
    lock = acquireLock(ctx.cwd);
  } catch (err) {
    return { stdout: "", stderr: `prune: lock timeout: ${msgOf(err)}`, exitCode: EXIT.LOCK_TIMEOUT };
  }

  try {
    const report = await executePrune(ctx, policy, dryRun);
    return {
      stdout: formatOutput(report, output),
      stderr: "",
      exitCode: EXIT.SUCCESS,
    };
  } catch (err) {
    return { stdout: "", stderr: `prune: ${msgOf(err)}`, exitCode: EXIT.IO_FAILURE };
  } finally {
    lock.release();
  }
}

async function executePrune(
  ctx: CliContext,
  policy: RetentionPolicy,
  dryRun: boolean,
): Promise<PruneReport> {
  const nowMs = Date.parse(ctx.now());
  const thresholdMs = nowMs - policy.retentionDays * 24 * 60 * 60 * 1000;

  const root = join(productionTracesRoot(ctx.cwd), "ingested");
  const gcLog = gcLogPath(ctx.cwd);

  let scannedFiles = 0;
  let scannedTraces = 0;
  let deletedTraces = 0;
  let preservedByCategory = 0;
  let preservedByAge = 0;

  if (policy.preserveAll) {
    return {
      dryRun,
      retentionDays: policy.retentionDays,
      scannedFiles: 0,
      scannedTraces: 0,
      deletedTraces: 0,
      preservedByCategory: 0,
      preservedByAge: 0,
      preserveAll: true,
    };
  }

  if (!existsSync(root)) {
    return {
      dryRun,
      retentionDays: policy.retentionDays,
      scannedFiles,
      scannedTraces,
      deletedTraces,
      preservedByCategory,
      preservedByAge,
      preserveAll: false,
    };
  }

  const preserve = new Set<string>(policy.preserveCategories);

  // Ensure gc-log directory exists before appending.
  if (!dryRun) {
    mkdirSync(productionTracesRoot(ctx.cwd), { recursive: true });
  }

  for (const date of readdirSync(root).sort()) {
    const dateDir = join(root, date);
    if (!statSync(dateDir).isDirectory()) continue;

    for (const file of readdirSync(dateDir).sort()) {
      if (!file.endsWith(".jsonl")) continue;
      const path = join(dateDir, file);
      scannedFiles += 1;

      const text = readFileSync(path, "utf-8");
      const lines = text.split("\n");
      const keep: string[] = [];
      for (const rawLine of lines) {
        if (rawLine.trim().length === 0) continue;
        scannedTraces += 1;
        let parsed: ProductionTrace;
        try {
          parsed = JSON.parse(rawLine) as ProductionTrace;
        } catch {
          // Malformed lines: preserve them so a later corrective ingest can
          // re-process. Never silently drop user data here.
          keep.push(rawLine);
          continue;
        }
        const endedMs = Date.parse(parsed.timing.endedAt);
        const tooYoung = Number.isNaN(endedMs) || endedMs > thresholdMs;
        if (tooYoung) {
          preservedByAge += 1;
          keep.push(rawLine);
          continue;
        }
        const label = parsed.outcome?.label;
        if (label !== undefined && preserve.has(label)) {
          preservedByCategory += 1;
          keep.push(rawLine);
          continue;
        }
        deletedTraces += 1;
        if (!dryRun) {
          // Append an audit record per deletion.
          appendFileSync(
            gcLog,
            JSON.stringify({
              traceId: parsed.traceId,
              deletedAt: ctx.now(),
              reason: "retention",
              fromFile: path,
            }) + "\n",
            "utf-8",
          );
        }
      }

      if (!dryRun) {
        if (keep.length === 0) {
          // Remove the file entirely (no remaining content).
          try {
            unlinkSync(path);
          } catch {
            // File may have been removed concurrently; ignore.
          }
        } else if (keep.length < lines.filter((l) => l.trim().length > 0).length) {
          writeFileSync(path, keep.join("\n") + "\n", "utf-8");
        }
      }
    }
  }

  return {
    dryRun,
    retentionDays: policy.retentionDays,
    scannedFiles,
    scannedTraces,
    deletedTraces,
    preservedByCategory,
    preservedByAge,
    preserveAll: false,
  };
}

function msgOf(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
