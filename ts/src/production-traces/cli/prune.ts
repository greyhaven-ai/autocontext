// `autoctx production-traces prune [--dry-run]`
//
// Thin wrapper over `retention/enforce.ts`. The real work lives in
// `production-traces/retention/` (spec §6.6 canonical home). This module is
// responsible only for:
//   - CLI flag parsing / help text
//   - Lock acquisition
//   - Translating the retention domain report into the legacy `PruneReport`
//     output shape that Layer 7 tests still consume
//
// LAYERING NOTE: Layer 7 shipped a provisional inline implementation here
// (see that commit message). Layer 8 extracted the core logic to
// `retention/enforce.ts` and reduced this file to orchestration only. All
// downstream retention consumers (ingest phase-2, future MCP tools) go
// through the retention module directly.

import { acquireLock } from "../ingest/lock.js";
import {
  enforceRetention,
  loadRetentionPolicy,
  type LoadedRetentionPolicy,
  type RetentionReport,
} from "../retention/index.js";
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

  let policy: LoadedRetentionPolicy;
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
    const nowUtc = new Date(ctx.now());
    const report = await enforceRetention({
      cwd: ctx.cwd,
      policy,
      nowUtc,
      dryRun,
    });
    return {
      stdout: formatOutput(toLegacyReport(dryRun, policy, report), output),
      stderr: "",
      exitCode: EXIT.SUCCESS,
    };
  } catch (err) {
    return { stdout: "", stderr: `prune: ${msgOf(err)}`, exitCode: EXIT.IO_FAILURE };
  } finally {
    lock.release();
  }
}

/**
 * Translate the canonical RetentionReport (from `retention/enforce.ts`) into
 * the legacy prune-CLI output shape. The field names here preserve the Layer
 * 7 JSON contract so existing tests and downstream consumers do not break.
 *
 * NOTE: `scannedFiles` is approximated — the canonical report surfaces
 * `batchesAffected` (files touched) rather than total files scanned. A
 * follow-up can bring the richer metric back to the CLI if operators need it.
 */
function toLegacyReport(
  dryRun: boolean,
  policy: LoadedRetentionPolicy,
  r: RetentionReport,
): PruneReport {
  return {
    dryRun,
    retentionDays: policy.retentionDays,
    scannedFiles: r.batchesAffected.length,
    scannedTraces: r.evaluated,
    // In dry-run the canonical report reports `deleted: 0` but `tooYoung +
    // preserved + "would-have-been-deleted"` equals `evaluated`. Operators
    // expect "deletedTraces" to show the candidate count in --dry-run too,
    // so we reconstruct it: eligible = evaluated - preserved - tooYoung.
    deletedTraces: dryRun ? r.evaluated - r.preserved - r.tooYoung : r.deleted,
    preservedByCategory: r.preserved,
    preservedByAge: r.tooYoung,
    preserveAll: policy.preserveAll,
  };
}

function msgOf(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
