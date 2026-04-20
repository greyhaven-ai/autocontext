// `autoctx production-traces ingest ...`
//
// Wraps Layer 3's `ingestBatches(cwd, opts)` with CLI flag parsing, exit-code
// mapping, and (optionally) a polling `--watch` loop.
//
// Lock acquisition note: `ingestBatches` itself acquires `.autocontext/lock`
// via `production-traces/ingest/lock.ts`. The CLI does NOT need to take the
// lock separately — doing so would deadlock. The lock scope is Foundation B-
// compatible: a concurrent control-plane `appendPromotionEvent` will block
// while ingest holds the lock, and vice versa (spec §6.2).
//
// Phase-2 retention: `ingestBatches` runs `enforceRetention` after the main
// ingest loop by default. The `--skip-retention` flag passes
// `retention: "skip"` through so operators can ingest without touching the
// retention subsystem (e.g. when debugging a phase-1 issue in isolation).

import { ingestBatches, type IngestReport } from "../ingest/scan-workflow.js";
import { EXIT } from "./_shared/exit-codes.js";
import { formatOutput, type OutputMode } from "./_shared/output-formatters.js";
import { parseFlags, stringFlag, booleanFlag } from "./_shared/flags.js";
import type { CliContext, CliResult } from "./_shared/types.js";

export const INGEST_HELP_TEXT = `autoctx production-traces ingest — scan incoming/ and validate traces

Usage:
  autoctx production-traces ingest
      [--since <iso-ts>]
      [--watch [--poll-interval <seconds>]]
      [--strict]
      [--dry-run]
      [--skip-retention]
      [--output json|pretty|table]

Behavior:
  Acquires .autocontext/lock (shared with Foundation B's registry).
  Walks incoming/<date>/*.jsonl, validates per-line, invokes redaction
  mark-at-ingest, moves successful batches to ingested/ and failed ones to
  failed/. Appends traceIds to seen-ids.jsonl to enforce idempotence.
  After phase-1, runs retention enforcement (spec §6.6) in the SAME lock
  scope unless --skip-retention is passed.

Flags:
  --since <ts>         Skip batches whose file mtime is before this ISO timestamp.
  --strict             Reject the whole batch if any line is invalid (spec §6.4).
  --dry-run            Validate + report without moving files or updating seen-ids.
  --skip-retention     Do not run phase-2 retention enforcement for this ingest.
  --watch              Polling loop; poll-interval seconds between scans.
  --poll-interval <N>  Watch-mode interval (default 30).
  --output <mode>      json | pretty | table (default pretty).

Exit codes:
  0   clean ingest (all batches succeeded without per-line failures)
  1   domain failure (ingest completed but with per-line errors and/or
      strict-mode batch rejections)
  2   partial success (some batches succeeded, some had line-level errors in
      non-strict mode — advisory signal for CI)
  10  lock timeout
  14  I/O failure
`;

const DEFAULT_POLL_INTERVAL_SEC = 30;

export async function runIngest(
  args: readonly string[],
  ctx: CliContext,
): Promise<CliResult> {
  if (args[0] === "--help" || args[0] === "-h") {
    return { stdout: INGEST_HELP_TEXT, stderr: "", exitCode: EXIT.SUCCESS };
  }
  const flags = parseFlags(args, {
    since: { type: "string" },
    strict: { type: "boolean" },
    "dry-run": { type: "boolean" },
    "skip-retention": { type: "boolean" },
    watch: { type: "boolean" },
    "poll-interval": { type: "string" },
    output: { type: "string", default: "pretty" },
  });
  if ("error" in flags) {
    return { stdout: "", stderr: flags.error, exitCode: EXIT.DOMAIN_FAILURE };
  }
  const output = (stringFlag(flags.value, "output") ?? "pretty") as OutputMode;
  const since = stringFlag(flags.value, "since");
  const strict = booleanFlag(flags.value, "strict");
  const dryRun = booleanFlag(flags.value, "dry-run");
  const skipRetention = booleanFlag(flags.value, "skip-retention");
  const watch = booleanFlag(flags.value, "watch");
  const pollRaw = stringFlag(flags.value, "poll-interval");
  const pollInterval =
    pollRaw === undefined ? DEFAULT_POLL_INTERVAL_SEC : Number.parseInt(pollRaw, 10);
  if (watch && (!Number.isFinite(pollInterval) || pollInterval <= 0)) {
    return {
      stdout: "",
      stderr: `--poll-interval must be a positive integer (got: ${pollRaw})`,
      exitCode: EXIT.DOMAIN_FAILURE,
    };
  }

  if (watch) {
    return runWatch(ctx, { since, strict, dryRun, skipRetention }, pollInterval, output);
  }

  return runOnce(ctx, { since, strict, dryRun, skipRetention }, output);
}

interface IngestFlags {
  readonly since: string | undefined;
  readonly strict: boolean;
  readonly dryRun: boolean;
  readonly skipRetention: boolean;
}

async function runOnce(
  ctx: CliContext,
  flags: IngestFlags,
  output: OutputMode,
): Promise<CliResult> {
  let report: IngestReport;
  try {
    report = await ingestBatches(ctx.cwd, {
      ...(flags.since !== undefined ? { since: flags.since } : {}),
      strict: flags.strict,
      dryRun: flags.dryRun,
      retention: flags.skipRetention ? "skip" : "enforce",
    });
  } catch (err) {
    return mapIngestError(err);
  }

  return {
    stdout: formatOutput(report, output),
    stderr: "",
    exitCode: pickIngestExitCode(report),
  };
}

/**
 * Watch loop: re-run ingestBatches on a timer until SIGINT/SIGTERM. On shutdown,
 * clears the timer and resolves with the last report. We deliberately emit a
 * JSON stream (one report per line) to stderr so operators can pipe stdout
 * elsewhere without dirtying it.
 *
 * CAVEAT: Lock contention during watch is intentionally fatal — the watch loop
 * does NOT back off and retry on lock-busy because that behavior would mask
 * concurrent-writer bugs. Operators should re-run manually if the lock was
 * held for legitimate reasons (Foundation B promotion mid-flight).
 */
async function runWatch(
  ctx: CliContext,
  flags: IngestFlags,
  pollIntervalSec: number,
  output: OutputMode,
): Promise<CliResult> {
  const stderrLines: string[] = [];
  let lastReport: IngestReport | null = null;

  return new Promise<CliResult>((resolve) => {
    let stopping = false;
    let inFlight: Promise<void> | null = null;

    const tick = async (): Promise<void> => {
      if (stopping) return;
      try {
        const report = await ingestBatches(ctx.cwd, {
          ...(flags.since !== undefined ? { since: flags.since } : {}),
          strict: flags.strict,
          dryRun: flags.dryRun,
          retention: flags.skipRetention ? "skip" : "enforce",
        });
        lastReport = report;
        stderrLines.push(`[watch] ${JSON.stringify(report)}`);
      } catch (err) {
        stopping = true;
        const mapped = mapIngestError(err);
        // Flush any prior watch output first.
        resolve({
          stdout: lastReport === null ? "" : formatOutput(lastReport, output),
          stderr: [...stderrLines, mapped.stderr].filter((l) => l.length > 0).join("\n"),
          exitCode: mapped.exitCode,
        });
      }
    };

    const shutdown = (): void => {
      if (stopping) return;
      stopping = true;
      clearInterval(handle);
      // Wait for the in-flight tick (if any), then resolve cleanly.
      const flush = inFlight ?? Promise.resolve();
      void flush.then(() => {
        resolve({
          stdout: lastReport === null ? "" : formatOutput(lastReport, output),
          stderr: stderrLines.join("\n"),
          exitCode: lastReport === null ? EXIT.SUCCESS : pickIngestExitCode(lastReport),
        });
      });
    };

    // First tick runs immediately; subsequent ticks every pollIntervalSec.
    inFlight = tick();
    const handle = setInterval(() => {
      inFlight = tick();
    }, pollIntervalSec * 1000);

    // Cleanup on SIGTERM/SIGINT so the test runner and real-world operators
    // exit cleanly. We listen via `process.once` to avoid registering
    // handlers on every watch invocation in a long-lived parent process.
    process.once("SIGTERM", shutdown);
    process.once("SIGINT", shutdown);
  });
}

function pickIngestExitCode(r: IngestReport): number {
  if (r.batchesFailedEntirely === 0 && r.linesRejected === 0) return EXIT.SUCCESS;
  if (r.batchesSucceeded > 0 && r.batchesFailedEntirely === 0) {
    // Non-strict per-line errors — advisory partial-success (spec §9.7).
    return EXIT.PARTIAL_SUCCESS;
  }
  return EXIT.DOMAIN_FAILURE;
}

function mapIngestError(err: unknown): CliResult {
  const msg = err instanceof Error ? err.message : String(err);
  if (msg.includes("lock already held") || msg.toLowerCase().includes("acquirelock")) {
    return { stdout: "", stderr: `ingest: lock timeout: ${msg}`, exitCode: EXIT.LOCK_TIMEOUT };
  }
  if (/redaction-policy|retention-policy/.test(msg)) {
    return { stdout: "", stderr: `ingest: invalid config: ${msg}`, exitCode: EXIT.INVALID_CONFIG };
  }
  return { stdout: "", stderr: `ingest: ${msg}`, exitCode: EXIT.IO_FAILURE };
}
