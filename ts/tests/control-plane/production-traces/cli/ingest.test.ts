import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, existsSync, readdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runProductionTracesCommand } from "../../../../src/production-traces/cli/index.js";
import { acquireLock } from "../../../../src/production-traces/ingest/lock.js";
import {
  ingestedDir,
  failedDir,
} from "../../../../src/production-traces/ingest/paths.js";
import { makeTrace, writeIncomingBatch, TEST_DATE } from "./_helpers/fixtures.js";

let cwd: string;

beforeEach(() => {
  cwd = mkdtempSync(join(tmpdir(), "autocontext-pt-cli-ingest-"));
});
afterEach(() => {
  rmSync(cwd, { recursive: true, force: true });
});

describe("autoctx production-traces ingest", () => {
  test("happy path: valid batch is ingested, exit 0, JSON report", async () => {
    writeIncomingBatch(cwd, TEST_DATE, "batch-ok", [makeTrace(), makeTrace()]);

    const r = await runProductionTracesCommand(
      ["ingest", "--output", "json"],
      { cwd },
    );
    expect(r.exitCode).toBe(0);
    const report = JSON.parse(r.stdout);
    expect(report.tracesIngested).toBe(2);
    expect(report.batchesSucceeded).toBe(1);
    expect(report.linesRejected).toBe(0);

    // File should have been moved to ingested/.
    expect(existsSync(join(ingestedDir(cwd, TEST_DATE), "batch-ok.jsonl"))).toBe(true);
  });

  test("--dry-run validates but does NOT mutate state", async () => {
    writeIncomingBatch(cwd, TEST_DATE, "batch-dry", [makeTrace()]);

    const r = await runProductionTracesCommand(
      ["ingest", "--dry-run", "--output", "json"],
      { cwd },
    );
    expect(r.exitCode).toBe(0);

    // ingested/ should not exist (or should be empty).
    const destDir = ingestedDir(cwd, TEST_DATE);
    if (existsSync(destDir)) {
      expect(readdirSync(destDir)).toHaveLength(0);
    }
  });

  test("lock contention yields exit 10", async () => {
    writeIncomingBatch(cwd, TEST_DATE, "batch-locked", [makeTrace()]);
    const holder = acquireLock(cwd);
    try {
      const r = await runProductionTracesCommand(["ingest"], { cwd });
      expect(r.exitCode).toBe(10);
    } finally {
      holder.release();
    }
  });

  test("partially-invalid batch in non-strict mode: advisory partial-success", async () => {
    const good = makeTrace();
    // Invalid line (malformed JSON) mixed with a valid trace.
    const dir = join(cwd, ".autocontext/production-traces/incoming", TEST_DATE);
    writeIncomingBatch(cwd, TEST_DATE, "batch-mixed", [good]);
    const { writeFileSync } = await import("node:fs");
    writeFileSync(
      join(dir, "batch-mixed.jsonl"),
      JSON.stringify(good) + "\nNOT JSON\n",
    );

    const r = await runProductionTracesCommand(
      ["ingest", "--output", "json"],
      { cwd },
    );
    // One line error + one success => spec §9.7 partial-success (exit 2).
    expect(r.exitCode).toBe(2);
    const report = JSON.parse(r.stdout);
    expect(report.tracesIngested).toBe(1);
    expect(report.linesRejected).toBe(1);
  });

  test("--strict rejects the whole batch on any per-line error → exit 1", async () => {
    const good = makeTrace();
    const dir = join(cwd, ".autocontext/production-traces/incoming", TEST_DATE);
    const { mkdirSync, writeFileSync } = await import("node:fs");
    mkdirSync(dir, { recursive: true });
    writeFileSync(
      join(dir, "batch-strict.jsonl"),
      JSON.stringify(good) + "\nNOT JSON\n",
    );

    const r = await runProductionTracesCommand(
      ["ingest", "--strict", "--output", "json"],
      { cwd },
    );
    expect(r.exitCode).toBe(1);
    const report = JSON.parse(r.stdout);
    expect(report.batchesFailedEntirely).toBe(1);
    expect(report.tracesIngested).toBe(0);
    // Batch moved to failed/.
    expect(existsSync(join(failedDir(cwd, TEST_DATE), "batch-strict.jsonl"))).toBe(true);
  });

  test("--help prints help and exits 0", async () => {
    const r = await runProductionTracesCommand(["ingest", "--help"], { cwd });
    expect(r.exitCode).toBe(0);
    expect(r.stdout.toLowerCase()).toContain("ingest");
  });

  test("rejects --poll-interval <= 0 with exit 1", async () => {
    const r = await runProductionTracesCommand(
      ["ingest", "--watch", "--poll-interval", "0"],
      { cwd },
    );
    expect(r.exitCode).toBe(1);
    expect(r.stderr).toContain("poll-interval");
  });
});
