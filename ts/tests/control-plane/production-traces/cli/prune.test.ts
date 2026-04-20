import { describe, test, expect, beforeEach, afterEach } from "vitest";
import {
  mkdtempSync,
  rmSync,
  existsSync,
  readFileSync,
  readdirSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runProductionTracesCommand } from "../../../../src/production-traces/cli/index.js";
import { acquireLock } from "../../../../src/production-traces/ingest/lock.js";
import {
  ingestedDir,
  gcLogPath,
} from "../../../../src/production-traces/ingest/paths.js";
import { makeTrace, writeIncomingBatch } from "./_helpers/fixtures.js";
import { newProductionTraceId } from "../../../../src/production-traces/contract/branded-ids.js";

let cwd: string;

beforeEach(() => {
  cwd = mkdtempSync(join(tmpdir(), "autocontext-pt-cli-prune-"));
});
afterEach(() => {
  rmSync(cwd, { recursive: true, force: true });
});

async function seedOldAndNewTraces(): Promise<{ oldId: string; newId: string }> {
  // Two traces: one 200 days old, one current. Default retention is 90d.
  const newOld = (daysAgo: number) => {
    const now = Date.parse("2026-04-17T12:00:00.000Z");
    return new Date(now - daysAgo * 24 * 60 * 60 * 1000).toISOString();
  };
  const oldId = newProductionTraceId();
  const newId = newProductionTraceId();
  // Old trace: stored under an old date partition.
  const oldDate = "2025-09-01"; // ~200 days before 2026-04-17
  writeIncomingBatch(cwd, oldDate, "batch-old", [
    makeTrace({
      traceId: oldId,
      startedAt: newOld(200),
      endedAt: newOld(200),
      outcome: { label: "success" },
    }),
  ]);
  writeIncomingBatch(cwd, "2026-04-17", "batch-new", [
    makeTrace({
      traceId: newId,
      startedAt: newOld(0),
      endedAt: newOld(0),
      outcome: { label: "success" },
    }),
  ]);
  // Layer 8: ingest now runs retention as phase-2 by default. These tests
  // exercise the out-of-band `prune` CLI, so they need the seed phase to
  // leave the old trace on disk. Pass --skip-retention so the seed call
  // writes the old batch to ingested/ unchanged.
  await runProductionTracesCommand(["ingest", "--skip-retention"], { cwd });
  return { oldId, newId };
}

describe("autoctx production-traces prune", () => {
  test("dry-run reports candidates without deleting", async () => {
    await runProductionTracesCommand(["init"], { cwd });
    await seedOldAndNewTraces();

    // Capture ingested files before.
    const oldDir = ingestedDir(cwd, "2025-09-01");
    const filesBefore = readdirSync(oldDir);
    expect(filesBefore.length).toBeGreaterThan(0);

    const r = await runProductionTracesCommand(
      ["prune", "--dry-run", "--output", "json"],
      { cwd, now: () => "2026-04-17T12:00:00.000Z" },
    );
    expect(r.exitCode).toBe(0);
    const report = JSON.parse(r.stdout);
    expect(report.dryRun).toBe(true);
    expect(report.deletedTraces).toBe(1);

    // Files must still exist.
    expect(readdirSync(oldDir)).toEqual(filesBefore);
    // gc-log must NOT have been written.
    expect(existsSync(gcLogPath(cwd))).toBe(false);
  });

  test("real run deletes eligible traces + appends to gc-log.jsonl", async () => {
    await runProductionTracesCommand(["init"], { cwd });
    await seedOldAndNewTraces();

    const r = await runProductionTracesCommand(
      ["prune", "--output", "json"],
      { cwd, now: () => "2026-04-17T12:00:00.000Z" },
    );
    expect(r.exitCode).toBe(0);
    const report = JSON.parse(r.stdout);
    expect(report.dryRun).toBe(false);
    expect(report.deletedTraces).toBe(1);

    expect(existsSync(gcLogPath(cwd))).toBe(true);
    const lines = readFileSync(gcLogPath(cwd), "utf-8").trim().split("\n");
    expect(lines.length).toBe(1);
    const entry = JSON.parse(lines[0]!);
    expect(entry.reason).toBe("retention-expired");
    expect(typeof entry.traceId).toBe("string");
  });

  test("preserveCategories 'failure' keeps matching traces", async () => {
    // Init + swap retention policy's preserveCategories to 'success'
    // so the seeded old trace (outcome.label=success) is preserved.
    await runProductionTracesCommand(["init"], { cwd });
    const { writeFileSync } = await import("node:fs");
    writeFileSync(
      join(cwd, ".autocontext/production-traces/retention-policy.json"),
      JSON.stringify({
        schemaVersion: "1.0",
        retentionDays: 90,
        preserveAll: false,
        preserveCategories: ["success"],
        gcBatchSize: 1000,
      }),
    );
    await seedOldAndNewTraces();

    const r = await runProductionTracesCommand(
      ["prune", "--output", "json"],
      { cwd, now: () => "2026-04-17T12:00:00.000Z" },
    );
    expect(r.exitCode).toBe(0);
    const report = JSON.parse(r.stdout);
    expect(report.deletedTraces).toBe(0);
    expect(report.preservedByCategory).toBe(1);
  });

  test("preserveAll: true short-circuits with zero deletions", async () => {
    await runProductionTracesCommand(["init"], { cwd });
    const { writeFileSync } = await import("node:fs");
    writeFileSync(
      join(cwd, ".autocontext/production-traces/retention-policy.json"),
      JSON.stringify({
        schemaVersion: "1.0",
        retentionDays: 90,
        preserveAll: true,
        preserveCategories: [],
        gcBatchSize: 1000,
      }),
    );
    await seedOldAndNewTraces();

    const r = await runProductionTracesCommand(
      ["prune", "--output", "json"],
      { cwd, now: () => "2026-04-17T12:00:00.000Z" },
    );
    expect(r.exitCode).toBe(0);
    const report = JSON.parse(r.stdout);
    expect(report.preserveAll).toBe(true);
    expect(report.deletedTraces).toBe(0);
  });

  test("lock contention yields exit 10", async () => {
    await runProductionTracesCommand(["init"], { cwd });
    const holder = acquireLock(cwd);
    try {
      const r = await runProductionTracesCommand(["prune"], { cwd });
      expect(r.exitCode).toBe(10);
    } finally {
      holder.release();
    }
  });

  test("--help exits 0", async () => {
    const r = await runProductionTracesCommand(["prune", "--help"], { cwd });
    expect(r.exitCode).toBe(0);
  });
});
