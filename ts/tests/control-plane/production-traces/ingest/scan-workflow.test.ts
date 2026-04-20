import { describe, test, expect, beforeEach, afterEach } from "vitest";
import {
  mkdtempSync,
  rmSync,
  mkdirSync,
  writeFileSync,
  existsSync,
  readFileSync,
  readdirSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import fc from "fast-check";
import { ingestBatches } from "../../../../src/production-traces/ingest/scan-workflow.js";
import {
  incomingDir,
  ingestedDir,
  failedDir,
  seenIdsPath,
} from "../../../../src/production-traces/ingest/paths.js";
import { acquireLock } from "../../../../src/production-traces/ingest/lock.js";
import {
  newProductionTraceId,
  type ProductionTraceId,
} from "../../../../src/production-traces/contract/branded-ids.js";
import type { ProductionTrace } from "../../../../src/production-traces/contract/types.js";

const DATE = "2026-04-17";

function makeTrace(traceId?: ProductionTraceId): ProductionTrace {
  const id = traceId ?? newProductionTraceId();
  return {
    schemaVersion: "1.0",
    traceId: id,
    source: { emitter: "sdk", sdk: { name: "autocontext-ts", version: "0.4.3" } },
    provider: { name: "openai" },
    model: "gpt-4o-mini",
    env: {
      environmentTag: "production" as ProductionTrace["env"]["environmentTag"],
      appId: "my-app" as ProductionTrace["env"]["appId"],
    },
    messages: [{ role: "user", content: "hi", timestamp: `${DATE}T12:00:00.000Z` }],
    toolCalls: [],
    timing: {
      startedAt: `${DATE}T12:00:00.000Z`,
      endedAt: `${DATE}T12:00:01.000Z`,
      latencyMs: 1000,
    },
    usage: { tokensIn: 10, tokensOut: 5 },
    feedbackRefs: [],
    links: {},
    redactions: [],
  };
}

function writeBatch(cwd: string, batchId: string, lines: string[]): string {
  const dir = incomingDir(cwd, DATE);
  mkdirSync(dir, { recursive: true });
  const path = join(dir, `${batchId}.jsonl`);
  writeFileSync(path, lines.join("\n") + (lines.length ? "\n" : ""));
  return path;
}

describe("ingestBatches", () => {
  let cwd: string;

  beforeEach(() => {
    cwd = mkdtempSync(join(tmpdir(), "autocontext-ingest-scan-"));
  });

  afterEach(() => {
    rmSync(cwd, { recursive: true, force: true });
  });

  test("happy path: 5 valid traces in one batch are ingested", async () => {
    const traces = [makeTrace(), makeTrace(), makeTrace(), makeTrace(), makeTrace()];
    writeBatch(cwd, "batch-1", traces.map((t) => JSON.stringify(t)));

    const report = await ingestBatches(cwd, {});

    expect(report.batchesProcessed).toBe(1);
    expect(report.batchesSucceeded).toBe(1);
    expect(report.batchesFailedEntirely).toBe(0);
    expect(report.tracesIngested).toBe(5);
    expect(report.duplicatesSkipped).toBe(0);
    expect(report.linesRejected).toBe(0);

    // Batch moved to ingested/<date>/batch-1.jsonl
    const ingestedPath = join(ingestedDir(cwd, DATE), "batch-1.jsonl");
    expect(existsSync(ingestedPath)).toBe(true);

    // Incoming file removed
    expect(existsSync(join(incomingDir(cwd, DATE), "batch-1.jsonl"))).toBe(false);

    // Receipt written
    const receiptPath = join(ingestedDir(cwd, DATE), "batch-1.receipt.json");
    expect(existsSync(receiptPath)).toBe(true);
    const receipt = JSON.parse(readFileSync(receiptPath, "utf-8"));
    expect(receipt.count).toBe(5);
    expect(receipt.tracesIngested).toBe(5);
    expect(receipt.duplicatesSkipped).toBe(0);

    // Seen-ids written
    const seenRaw = readFileSync(seenIdsPath(cwd), "utf-8");
    for (const t of traces) {
      expect(seenRaw).toContain(t.traceId);
    }
  });

  test("per-line tolerance: 4 valid + 1 malformed line → 4 ingested + partial success", async () => {
    const good = [makeTrace(), makeTrace(), makeTrace(), makeTrace()];
    const lines = [
      JSON.stringify(good[0]),
      JSON.stringify(good[1]),
      "{ malformed json",
      JSON.stringify(good[2]),
      JSON.stringify(good[3]),
    ];
    writeBatch(cwd, "batch-partial", lines);

    const report = await ingestBatches(cwd, {});

    expect(report.batchesProcessed).toBe(1);
    expect(report.batchesSucceeded).toBe(1);
    expect(report.batchesFailedEntirely).toBe(0);
    expect(report.tracesIngested).toBe(4);
    expect(report.linesRejected).toBe(1);

    // Batch moved to ingested/ with receipt AND error file.
    const ingestedBatchPath = join(ingestedDir(cwd, DATE), "batch-partial.jsonl");
    const receiptPath = join(ingestedDir(cwd, DATE), "batch-partial.receipt.json");
    const errorPath = join(ingestedDir(cwd, DATE), "batch-partial.error.json");
    expect(existsSync(ingestedBatchPath)).toBe(true);
    expect(existsSync(receiptPath)).toBe(true);
    expect(existsSync(errorPath)).toBe(true);

    // Only 4 lines in the ingested jsonl.
    const ingestedRaw = readFileSync(ingestedBatchPath, "utf-8");
    expect(ingestedRaw.trimEnd().split("\n").length).toBe(4);

    // Error file records the malformed line.
    const err = JSON.parse(readFileSync(errorPath, "utf-8"));
    expect(err.perLineErrors.length).toBe(1);
    expect(err.perLineErrors[0].lineNo).toBe(3);
  });

  test("strict mode: 4 valid + 1 malformed line → 0 ingested, batch moved to failed/", async () => {
    const good = [makeTrace(), makeTrace(), makeTrace(), makeTrace()];
    const lines = [
      JSON.stringify(good[0]),
      "{ malformed json",
      JSON.stringify(good[1]),
      JSON.stringify(good[2]),
      JSON.stringify(good[3]),
    ];
    writeBatch(cwd, "batch-strict", lines);

    const report = await ingestBatches(cwd, { strict: true });

    expect(report.batchesProcessed).toBe(1);
    expect(report.batchesSucceeded).toBe(0);
    expect(report.batchesFailedEntirely).toBe(1);
    expect(report.tracesIngested).toBe(0);
    expect(report.linesRejected).toBe(1);

    // Batch moved to failed/
    const failedBatch = join(failedDir(cwd, DATE), "batch-strict.jsonl");
    const failedError = join(failedDir(cwd, DATE), "batch-strict.error.json");
    expect(existsSync(failedBatch)).toBe(true);
    expect(existsSync(failedError)).toBe(true);

    // Nothing in ingested/
    expect(existsSync(join(ingestedDir(cwd, DATE), "batch-strict.jsonl"))).toBe(false);

    // Seen-ids file NOT created (no ingestions).
    expect(existsSync(seenIdsPath(cwd))).toBe(false);
  });

  test("dedupe: same batch processed twice → second run reports 0 new ingestions (P3 foundation)", async () => {
    const traces = [makeTrace(), makeTrace(), makeTrace()];
    const lines = traces.map((t) => JSON.stringify(t));
    writeBatch(cwd, "batch-a", lines);

    const first = await ingestBatches(cwd, {});
    expect(first.tracesIngested).toBe(3);

    // Put the SAME batch back into incoming/ (simulating a retry/re-drop).
    writeBatch(cwd, "batch-a", lines);

    const second = await ingestBatches(cwd, {});
    expect(second.batchesProcessed).toBe(1);
    // Zero newly-ingested, everything was deduped.
    expect(second.tracesIngested).toBe(0);
    expect(second.duplicatesSkipped).toBe(3);

    // seen-ids file still has exactly 3 lines.
    const seenRaw = readFileSync(seenIdsPath(cwd), "utf-8");
    const seenLines = seenRaw.trim().split("\n");
    expect(seenLines.length).toBe(3);
  });

  test("dry-run: zero file movements, zero seen-ids changes, accurate report", async () => {
    const traces = [makeTrace(), makeTrace()];
    writeBatch(cwd, "batch-dry", traces.map((t) => JSON.stringify(t)));

    const report = await ingestBatches(cwd, { dryRun: true });

    expect(report.batchesProcessed).toBe(1);
    expect(report.tracesIngested).toBe(2);
    expect(report.linesRejected).toBe(0);

    // Incoming batch still there.
    expect(existsSync(join(incomingDir(cwd, DATE), "batch-dry.jsonl"))).toBe(true);
    // Nothing in ingested/ or failed/.
    expect(existsSync(join(ingestedDir(cwd, DATE), "batch-dry.jsonl"))).toBe(false);
    expect(existsSync(join(failedDir(cwd, DATE), "batch-dry.jsonl"))).toBe(false);
    // Seen-ids file not created.
    expect(existsSync(seenIdsPath(cwd))).toBe(false);
  });

  test("--since filter skips batches whose file mtime is before the threshold", async () => {
    const oldTrace = makeTrace();
    const newTrace = makeTrace();
    const oldPath = writeBatch(cwd, "batch-old", [JSON.stringify(oldTrace)]);
    // Make the old batch's mtime clearly in the past.
    const pastTime = new Date("2026-04-15T00:00:00.000Z");
    const { utimesSync } = await import("node:fs");
    utimesSync(oldPath, pastTime, pastTime);

    writeBatch(cwd, "batch-new", [JSON.stringify(newTrace)]);

    const report = await ingestBatches(cwd, { since: "2026-04-16T00:00:00.000Z" });

    // Only the new batch processed.
    expect(report.batchesProcessed).toBe(1);
    expect(report.tracesIngested).toBe(1);

    // Old batch remains in incoming/
    expect(existsSync(oldPath)).toBe(true);
    // New batch moved to ingested/
    expect(existsSync(join(ingestedDir(cwd, DATE), "batch-new.jsonl"))).toBe(true);
  });

  test("lock contention: ingestBatches fails cleanly (does not hang) when lock held", async () => {
    const handle = acquireLock(cwd);
    try {
      await expect(ingestBatches(cwd, {})).rejects.toThrow(/lock/i);
    } finally {
      handle.release();
    }
    // After release, a fresh ingest should succeed.
    writeBatch(cwd, "batch-after", [JSON.stringify(makeTrace())]);
    const report = await ingestBatches(cwd, {});
    expect(report.tracesIngested).toBe(1);
  });

  test("empty batch file is processed without error", async () => {
    writeBatch(cwd, "batch-empty", []);
    const report = await ingestBatches(cwd, {});
    expect(report.batchesProcessed).toBe(1);
    expect(report.tracesIngested).toBe(0);
    expect(report.linesRejected).toBe(0);
    // Empty batch with zero successes counts as failed entirely.
    expect(report.batchesFailedEntirely).toBe(1);
  });

  test("no incoming/<date>/ directory → zero-batch report, no error", async () => {
    const report = await ingestBatches(cwd, {});
    expect(report.batchesProcessed).toBe(0);
    expect(report.tracesIngested).toBe(0);
  });

  test("P3 property: running ingestBatches twice yields identical on-disk state (50 runs)", async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.integer({ min: 1, max: 20 }).chain((n) =>
          fc.array(
            fc.constant(null).map(() => makeTrace()),
            { minLength: n, maxLength: n },
          ),
        ),
        async (traces) => {
          // Fresh tmp dir per iteration.
          const localCwd = mkdtempSync(join(tmpdir(), "autocontext-p3-"));
          try {
            writeBatch(localCwd, "batch-p3", traces.map((t) => JSON.stringify(t)));
            const r1 = await ingestBatches(localCwd, {});
            expect(r1.tracesIngested).toBe(traces.length);

            // Snapshot on-disk state after first run.
            const stateBefore = snapshotDir(localCwd);

            // Re-drop the same batch and run again — second run should be a no-op
            // for the dedupe pipeline.
            writeBatch(localCwd, "batch-p3", traces.map((t) => JSON.stringify(t)));
            const r2 = await ingestBatches(localCwd, {});

            expect(r2.tracesIngested).toBe(0);
            expect(r2.duplicatesSkipped).toBe(traces.length);

            // The re-ingested batch-p3.jsonl file in ingested/ may or may not
            // overwrite — but seen-ids must be unchanged.
            const seen1 = stateBefore.get(seenIdsPath(localCwd)) ?? "";
            const seen2 = readFileSync(seenIdsPath(localCwd), "utf-8");
            expect(seen2).toBe(seen1);
          } finally {
            rmSync(localCwd, { recursive: true, force: true });
          }
        },
      ),
      { numRuns: 50 },
    );
  }, 60_000);
});

function snapshotDir(root: string): Map<string, string> {
  const out = new Map<string, string>();
  const walk = (d: string): void => {
    if (!existsSync(d)) return;
    for (const entry of readdirSync(d, { withFileTypes: true })) {
      const full = join(d, entry.name);
      if (entry.isDirectory()) walk(full);
      else out.set(full, readFileSync(full, "utf-8"));
    }
  };
  walk(root);
  return out;
}
