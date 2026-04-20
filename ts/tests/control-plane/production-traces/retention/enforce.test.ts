import { describe, test, expect, beforeEach, afterEach } from "vitest";
import {
  mkdtempSync,
  rmSync,
  readFileSync,
  existsSync,
  readdirSync,
  mkdirSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  enforceRetention,
  defaultRetentionPolicy,
  saveRetentionPolicy,
  type RetentionPolicy,
  type RetentionReport,
  readGcLog,
} from "../../../../src/production-traces/retention/index.js";
import {
  ingestedDir,
  gcLogPath,
} from "../../../../src/production-traces/ingest/paths.js";
import type { ProductionTrace } from "../../../../src/production-traces/contract/types.js";
import { newProductionTraceId } from "../../../../src/production-traces/contract/branded-ids.js";

let cwd: string;

beforeEach(() => {
  cwd = mkdtempSync(join(tmpdir(), "autocontext-pt-retention-enforce-"));
});
afterEach(() => {
  rmSync(cwd, { recursive: true, force: true });
});

/** Deterministic reference "now". */
const NOW = new Date("2026-04-17T12:00:00.000Z");

/** Subtract N days from NOW; return ISO string. */
function daysAgo(n: number): string {
  return new Date(NOW.getTime() - n * 24 * 60 * 60 * 1000).toISOString();
}

/** Build a trace with the given endedAt and optional outcome label. */
function trace(opts: {
  endedAt: string;
  label?: ProductionTrace["outcome"] extends undefined ? never : "success" | "failure" | "partial" | "unknown";
}): ProductionTrace {
  const traceId = newProductionTraceId();
  const endedMs = Date.parse(opts.endedAt);
  const startedAt = new Date(endedMs - 1000).toISOString();
  return {
    schemaVersion: "1.0",
    traceId,
    source: { emitter: "sdk", sdk: { name: "autoctx-ts", version: "0.4.3" } },
    provider: { name: "openai" },
    model: "gpt-4o-mini",
    env: {
      environmentTag: "production" as ProductionTrace["env"]["environmentTag"],
      appId: "my-app" as ProductionTrace["env"]["appId"],
    },
    messages: [{ role: "user", content: "hi", timestamp: startedAt }],
    toolCalls: [],
    timing: { startedAt, endedAt: opts.endedAt, latencyMs: 1000 },
    usage: { tokensIn: 10, tokensOut: 5 },
    feedbackRefs: [],
    links: {},
    redactions: [],
    ...(opts.label !== undefined ? { outcome: { label: opts.label } } : {}),
  };
}

/** Write a JSONL batch under ingested/<date>/<batch>.jsonl. */
function writeIngestedBatch(date: string, batchId: string, traces: ProductionTrace[]): string {
  const dir = ingestedDir(cwd, date);
  mkdirSync(dir, { recursive: true });
  const path = join(dir, `${batchId}.jsonl`);
  writeFileSync(path, traces.map((t) => JSON.stringify(t)).join("\n") + "\n", "utf-8");
  return path;
}

describe("retention/enforce", () => {
  test("deletes traces older than retentionDays, keeps the rest", async () => {
    const oldTrace = trace({ endedAt: daysAgo(200), label: "success" });
    const newTrace = trace({ endedAt: daysAgo(10), label: "success" });
    writeIngestedBatch("2025-09-01", "batch-mixed", [oldTrace, newTrace]);

    const report = await enforceRetention({
      cwd,
      policy: defaultRetentionPolicy(),
      nowUtc: NOW,
      dryRun: false,
    });

    expect(report.evaluated).toBe(2);
    expect(report.deleted).toBe(1);
    expect(report.tooYoung).toBe(1);
    expect(report.preserved).toBe(0);
    expect(report.gcLogEntriesAppended).toBe(1);

    // Batch file should now contain only the new trace.
    const body = readFileSync(
      join(ingestedDir(cwd, "2025-09-01"), "batch-mixed.jsonl"),
      "utf-8",
    ).trim();
    const remaining = body.split("\n").map((l) => JSON.parse(l));
    expect(remaining.length).toBe(1);
    expect(remaining[0].traceId).toBe(newTrace.traceId);
  });

  test("preserveCategories retains matching traces regardless of age", async () => {
    const oldFailure = trace({ endedAt: daysAgo(200), label: "failure" });
    const oldSuccess = trace({ endedAt: daysAgo(200), label: "success" });
    writeIngestedBatch("2025-09-01", "b", [oldFailure, oldSuccess]);

    const report = await enforceRetention({
      cwd,
      policy: defaultRetentionPolicy(), // preserveCategories: ["failure"]
      nowUtc: NOW,
      dryRun: false,
    });

    expect(report.deleted).toBe(1);
    expect(report.preserved).toBe(1);
    expect(report.tooYoung).toBe(0);

    const body = readFileSync(
      join(ingestedDir(cwd, "2025-09-01"), "b.jsonl"),
      "utf-8",
    ).trim();
    const remaining = body.split("\n").map((l) => JSON.parse(l));
    expect(remaining.length).toBe(1);
    expect(remaining[0].traceId).toBe(oldFailure.traceId);
  });

  test("preserveAll: true short-circuits — no traces touched", async () => {
    const oldTrace = trace({ endedAt: daysAgo(500), label: "success" });
    writeIngestedBatch("2024-12-01", "b", [oldTrace]);

    const policy: RetentionPolicy = {
      ...defaultRetentionPolicy(),
      preserveAll: true,
    };
    const report = await enforceRetention({ cwd, policy, nowUtc: NOW, dryRun: false });

    expect(report.evaluated).toBe(0);
    expect(report.deleted).toBe(0);
    expect(report.batchesAffected).toEqual([]);
    // Batch untouched.
    const body = readFileSync(
      join(ingestedDir(cwd, "2024-12-01"), "b.jsonl"),
      "utf-8",
    );
    expect(body.trim().split("\n").length).toBe(1);
    // gc-log untouched.
    expect(existsSync(gcLogPath(cwd))).toBe(false);
  });

  test("dryRun: true produces the same report but makes zero changes", async () => {
    const oldTrace = trace({ endedAt: daysAgo(200), label: "success" });
    const newTrace = trace({ endedAt: daysAgo(10), label: "success" });
    writeIngestedBatch("2025-09-01", "b", [oldTrace, newTrace]);

    const before = readFileSync(join(ingestedDir(cwd, "2025-09-01"), "b.jsonl"), "utf-8");

    const report = await enforceRetention({
      cwd,
      policy: defaultRetentionPolicy(),
      nowUtc: NOW,
      dryRun: true,
    });

    expect(report.evaluated).toBe(2);
    // "deleted" counts *would-be* deletions in dry-run.
    expect(report.deleted).toBe(0);
    expect(report.tooYoung).toBe(1);
    expect(report.gcLogEntriesAppended).toBe(0);

    // File bytes must be unchanged.
    const after = readFileSync(join(ingestedDir(cwd, "2025-09-01"), "b.jsonl"), "utf-8");
    expect(after).toBe(before);
    // gc-log must not exist.
    expect(existsSync(gcLogPath(cwd))).toBe(false);
  });

  test("batch rewrite preserves non-deleted trace bytes exactly", async () => {
    const t1 = trace({ endedAt: daysAgo(10), label: "success" });
    const t2Old = trace({ endedAt: daysAgo(300), label: "success" });
    const t3 = trace({ endedAt: daysAgo(20), label: "failure" });
    writeIngestedBatch("2026-03-01", "mixed", [t1, t2Old, t3]);

    // Capture the exact on-disk byte representation of the lines we expect to keep.
    const originalBody = readFileSync(
      join(ingestedDir(cwd, "2026-03-01"), "mixed.jsonl"),
      "utf-8",
    );
    const originalLines = originalBody.split("\n").filter((l) => l.trim().length > 0);
    const keepLines = originalLines.filter(
      (l) => !l.includes(t2Old.traceId),
    );

    await enforceRetention({
      cwd,
      policy: defaultRetentionPolicy(),
      nowUtc: NOW,
      dryRun: false,
    });

    const after = readFileSync(
      join(ingestedDir(cwd, "2026-03-01"), "mixed.jsonl"),
      "utf-8",
    );
    expect(after).toBe(keepLines.join("\n") + "\n");
  });

  test("gcBatchSize bounds work per run", async () => {
    // Seed 10 old + 1 new traces in a single batch.
    const oldTraces = Array.from({ length: 10 }, () =>
      trace({ endedAt: daysAgo(200), label: "success" }),
    );
    const newTrace = trace({ endedAt: daysAgo(10), label: "success" });
    writeIngestedBatch("2025-09-01", "big", [...oldTraces, newTrace]);

    const policy: RetentionPolicy = { ...defaultRetentionPolicy(), gcBatchSize: 3 };
    const report = await enforceRetention({ cwd, policy, nowUtc: NOW, dryRun: false });

    // First run must not exceed gcBatchSize deletions, even though more are eligible.
    expect(report.deleted).toBeLessThanOrEqual(3);
    // New trace remains regardless.
    const after = readFileSync(
      join(ingestedDir(cwd, "2025-09-01"), "big.jsonl"),
      "utf-8",
    );
    expect(after).toContain(newTrace.traceId);
  });

  test("gc-log entries use the spec vocabulary (reason: 'retention-expired')", async () => {
    const oldTrace = trace({ endedAt: daysAgo(200), label: "success" });
    writeIngestedBatch("2025-09-01", "b", [oldTrace]);

    await enforceRetention({
      cwd,
      policy: defaultRetentionPolicy(),
      nowUtc: NOW,
      dryRun: false,
    });

    const entries = readGcLog(cwd);
    expect(entries.length).toBe(1);
    expect(entries[0]!.reason).toBe("retention-expired");
    expect(entries[0]!.traceId).toBe(oldTrace.traceId);
    expect(entries[0]!.deletedAt).toBe(NOW.toISOString());
    expect(entries[0]!.batchPath).toContain("ingested/2025-09-01");
  });

  test("deterministic: same inputs + same nowUtc produce identical report and files", async () => {
    // Two sibling tmpdirs get the same fixture; enforce in each; compare output.
    const setupA = mkdtempSync(join(tmpdir(), "autocontext-pt-det-a-"));
    const setupB = mkdtempSync(join(tmpdir(), "autocontext-pt-det-b-"));
    try {
      const fixture = [
        trace({ endedAt: daysAgo(200), label: "success" }),
        trace({ endedAt: daysAgo(10), label: "success" }),
        trace({ endedAt: daysAgo(250), label: "failure" }),
      ];
      for (const root of [setupA, setupB]) {
        const dir = ingestedDir(root, "2025-09-01");
        mkdirSync(dir, { recursive: true });
        writeFileSync(
          join(dir, "b.jsonl"),
          fixture.map((t) => JSON.stringify(t)).join("\n") + "\n",
          "utf-8",
        );
      }
      const policy = defaultRetentionPolicy();
      const reportA = await enforceRetention({ cwd: setupA, policy, nowUtc: NOW, dryRun: false });
      const reportB = await enforceRetention({ cwd: setupB, policy, nowUtc: NOW, dryRun: false });
      expect(reportA).toEqual(reportB);

      const fileA = readFileSync(join(ingestedDir(setupA, "2025-09-01"), "b.jsonl"), "utf-8");
      const fileB = readFileSync(join(ingestedDir(setupB, "2025-09-01"), "b.jsonl"), "utf-8");
      expect(fileA).toBe(fileB);
      const gcA = readFileSync(gcLogPath(setupA), "utf-8");
      const gcB = readFileSync(gcLogPath(setupB), "utf-8");
      expect(gcA).toBe(gcB);
    } finally {
      rmSync(setupA, { recursive: true, force: true });
      rmSync(setupB, { recursive: true, force: true });
    }
  });

  test("empty ingested/ tree reports zeros and does not create gc-log", async () => {
    const report = await enforceRetention({
      cwd,
      policy: defaultRetentionPolicy(),
      nowUtc: NOW,
      dryRun: false,
    });
    expect(report).toEqual<RetentionReport>({
      evaluated: 0,
      deleted: 0,
      preserved: 0,
      tooYoung: 0,
      batchesAffected: [],
      gcLogEntriesAppended: 0,
    });
    expect(existsSync(gcLogPath(cwd))).toBe(false);
  });

  test("batch with all traces deleted is removed from disk", async () => {
    const t1 = trace({ endedAt: daysAgo(200), label: "success" });
    const t2 = trace({ endedAt: daysAgo(250), label: "success" });
    writeIngestedBatch("2025-08-01", "all-old", [t1, t2]);

    await enforceRetention({
      cwd,
      policy: defaultRetentionPolicy(),
      nowUtc: NOW,
      dryRun: false,
    });

    const dir = ingestedDir(cwd, "2025-08-01");
    const files = existsSync(dir) ? readdirSync(dir) : [];
    // The batch file must be gone (empty file would be misleading).
    expect(files.includes("all-old.jsonl")).toBe(false);
  });

  test("preserveCategories array with no matching labels leaves nothing preserved", async () => {
    const traceNoLabel = trace({ endedAt: daysAgo(200) });
    writeIngestedBatch("2025-09-01", "b", [traceNoLabel]);

    const policy: RetentionPolicy = {
      ...defaultRetentionPolicy(),
      preserveCategories: [], // nothing preserved
    };
    const report = await enforceRetention({ cwd, policy, nowUtc: NOW, dryRun: false });
    expect(report.deleted).toBe(1);
  });

  test("dryRun short-circuit via preserveAll: true still returns zero-report", async () => {
    const oldTrace = trace({ endedAt: daysAgo(500), label: "success" });
    writeIngestedBatch("2024-12-01", "b", [oldTrace]);

    const policy: RetentionPolicy = {
      ...defaultRetentionPolicy(),
      preserveAll: true,
    };
    const report = await enforceRetention({ cwd, policy, nowUtc: NOW, dryRun: true });
    expect(report.deleted).toBe(0);
    expect(report.evaluated).toBe(0);
  });

  test("malformed JSONL line is preserved, not silently dropped", async () => {
    const valid = trace({ endedAt: daysAgo(10), label: "success" });
    const dir = ingestedDir(cwd, "2026-04-07");
    mkdirSync(dir, { recursive: true });
    const path = join(dir, "mixed.jsonl");
    writeFileSync(
      path,
      JSON.stringify(valid) + "\n" + "{not-json-garbage\n",
      "utf-8",
    );

    const report = await enforceRetention({
      cwd,
      policy: defaultRetentionPolicy(),
      nowUtc: NOW,
      dryRun: false,
    });

    // Malformed line isn't counted as "evaluated" but isn't deleted either.
    expect(report.deleted).toBe(0);
    const after = readFileSync(path, "utf-8");
    // The garbage line must still be present.
    expect(after).toContain("{not-json-garbage");
  });

  test("saved-to-disk retention policy round-trips through loader", async () => {
    // Smoke test combining policy load + enforcement.
    const custom: RetentionPolicy = {
      schemaVersion: "1.0",
      retentionDays: 7,
      preserveAll: false,
      preserveCategories: ["partial"],
      gcBatchSize: 50,
    };
    await saveRetentionPolicy(cwd, custom);
    const oldPartial = trace({ endedAt: daysAgo(30), label: "partial" });
    const oldSuccess = trace({ endedAt: daysAgo(30), label: "success" });
    writeIngestedBatch("2026-03-18", "b", [oldPartial, oldSuccess]);

    const { loadRetentionPolicy } = await import(
      "../../../../src/production-traces/retention/index.js"
    );
    const loaded = await loadRetentionPolicy(cwd);
    const report = await enforceRetention({
      cwd,
      policy: loaded,
      nowUtc: NOW,
      dryRun: false,
    });
    expect(report.preserved).toBe(1);
    expect(report.deleted).toBe(1);
  });
});
