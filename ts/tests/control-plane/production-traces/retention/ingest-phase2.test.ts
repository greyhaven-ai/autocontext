// Phase-2 integration: `ingestBatches` runs `enforceRetention` inside the
// same lock scope, by default. Callers opt out with `retention: "skip"`.
//
// Spec §6.3 ends with: "run retention enforcement (phase 2 of same lock
// scope)" — these tests pin that contract.

import { describe, test, expect, beforeEach, afterEach } from "vitest";
import {
  mkdtempSync,
  rmSync,
  existsSync,
  readFileSync,
  mkdirSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  ingestBatches,
  type IngestReport,
} from "../../../../src/production-traces/ingest/scan-workflow.js";
import {
  ingestedDir,
  gcLogPath,
} from "../../../../src/production-traces/ingest/paths.js";
import {
  saveRetentionPolicy,
  defaultRetentionPolicy,
} from "../../../../src/production-traces/retention/index.js";
import {
  saveRedactionPolicy,
  defaultRedactionPolicy,
} from "../../../../src/production-traces/redaction/index.js";
import { newProductionTraceId } from "../../../../src/production-traces/contract/branded-ids.js";
import type { ProductionTrace } from "../../../../src/production-traces/contract/types.js";
import { makeTrace, writeIncomingBatch } from "../cli/_helpers/fixtures.js";

let cwd: string;

beforeEach(async () => {
  cwd = mkdtempSync(join(tmpdir(), "autocontext-pt-ingest-phase2-"));
  // Seed minimum policies so ingestBatches doesn't throw during setup.
  await saveRedactionPolicy(cwd, defaultRedactionPolicy());
  await saveRetentionPolicy(cwd, defaultRetentionPolicy());
});
afterEach(() => {
  rmSync(cwd, { recursive: true, force: true });
});

describe("ingest phase-2 retention hook", () => {
  test("default retention: 'enforce' runs retention after ingest within same lock scope", async () => {
    // Seed an already-ingested OLD trace that should be deleted by phase-2.
    const oldTrace = makeTrace({
      traceId: newProductionTraceId(),
      startedAt: "2025-09-01T00:00:00.000Z",
      endedAt: "2025-09-01T00:00:01.000Z",
      outcome: { label: "success" },
    });
    const oldDir = ingestedDir(cwd, "2025-09-01");
    mkdirSync(oldDir, { recursive: true });
    writeFileSync(
      join(oldDir, "preexisting.jsonl"),
      JSON.stringify(oldTrace) + "\n",
      "utf-8",
    );

    // Also drop a NEW incoming batch that phase-1 will ingest normally.
    const newTrace = makeTrace({
      traceId: newProductionTraceId(),
      startedAt: "2026-04-17T11:00:00.000Z",
      endedAt: "2026-04-17T11:00:01.000Z",
      outcome: { label: "success" },
    });
    writeIncomingBatch(cwd, "2026-04-17", "newest", [newTrace]);

    const report = await ingestBatches(cwd, {});

    // Phase-1 results.
    expect(report.tracesIngested).toBe(1);
    // Phase-2 report present (non-null by default).
    expect(report.retention).not.toBeNull();
    expect(report.retention!.deleted).toBeGreaterThanOrEqual(1);
    // gc-log must exist with at least one entry.
    expect(existsSync(gcLogPath(cwd))).toBe(true);
    const gcLines = readFileSync(gcLogPath(cwd), "utf-8").trim().split("\n");
    expect(gcLines.length).toBeGreaterThanOrEqual(1);
    // Old batch should have been deleted (the single old trace rewrites
    // the file to empty and unlinks it).
    expect(existsSync(join(oldDir, "preexisting.jsonl"))).toBe(false);
  });

  test("retention: 'skip' preserves ingested/ and does not touch gc-log", async () => {
    // Same pre-existing old trace as above.
    const oldTrace = makeTrace({
      traceId: newProductionTraceId(),
      startedAt: "2025-09-01T00:00:00.000Z",
      endedAt: "2025-09-01T00:00:01.000Z",
      outcome: { label: "success" },
    });
    const oldDir = ingestedDir(cwd, "2025-09-01");
    mkdirSync(oldDir, { recursive: true });
    writeFileSync(
      join(oldDir, "preexisting.jsonl"),
      JSON.stringify(oldTrace) + "\n",
      "utf-8",
    );

    const report = await ingestBatches(cwd, { retention: "skip" });

    // Retention phase skipped.
    expect(report.retention).toBeNull();
    // Pre-existing old trace is still on disk.
    expect(existsSync(join(oldDir, "preexisting.jsonl"))).toBe(true);
    // gc-log was never touched.
    expect(existsSync(gcLogPath(cwd))).toBe(false);
  });

  test("retention: 'enforce' respects preserveAll: true (no deletions)", async () => {
    // Seed old trace + preserveAll-true policy.
    const oldTrace: ProductionTrace = makeTrace({
      traceId: newProductionTraceId(),
      startedAt: "2024-01-01T00:00:00.000Z",
      endedAt: "2024-01-01T00:00:01.000Z",
      outcome: { label: "success" },
    });
    const oldDir = ingestedDir(cwd, "2024-01-01");
    mkdirSync(oldDir, { recursive: true });
    writeFileSync(
      join(oldDir, "preexisting.jsonl"),
      JSON.stringify(oldTrace) + "\n",
      "utf-8",
    );
    await saveRetentionPolicy(cwd, {
      ...defaultRetentionPolicy(),
      preserveAll: true,
    });

    const report = await ingestBatches(cwd, {});

    expect(report.retention).not.toBeNull();
    expect(report.retention!.deleted).toBe(0);
    expect(existsSync(join(oldDir, "preexisting.jsonl"))).toBe(true);
  });

  test("dryRun: true propagates to retention as dryRun (no file changes)", async () => {
    const oldTrace = makeTrace({
      traceId: newProductionTraceId(),
      startedAt: "2025-09-01T00:00:00.000Z",
      endedAt: "2025-09-01T00:00:01.000Z",
      outcome: { label: "success" },
    });
    const oldDir = ingestedDir(cwd, "2025-09-01");
    mkdirSync(oldDir, { recursive: true });
    writeFileSync(
      join(oldDir, "preexisting.jsonl"),
      JSON.stringify(oldTrace) + "\n",
      "utf-8",
    );

    const report = await ingestBatches(cwd, { dryRun: true });

    // Phase-1 was dry; phase-2 should also be dry (or skipped for dryRun) —
    // either way, the old trace must remain on disk.
    expect(existsSync(join(oldDir, "preexisting.jsonl"))).toBe(true);
    expect(existsSync(gcLogPath(cwd))).toBe(false);
    // Type-shape still observable.
    const _check: IngestReport = report;
    expect(typeof _check.tracesIngested).toBe("number");
  });
});
