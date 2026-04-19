import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, readFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runProductionTracesCommand } from "../../../../src/production-traces/cli/index.js";
import { makeTrace, writeIncomingBatch, TEST_DATE } from "./_helpers/fixtures.js";
import { newProductionTraceId } from "../../../../src/production-traces/contract/branded-ids.js";

let cwd: string;

async function seed(count = 2): Promise<void> {
  const traces = Array.from({ length: count }, () => makeTrace({ traceId: newProductionTraceId() }));
  writeIncomingBatch(cwd, TEST_DATE, "batch-exp", traces);
  await runProductionTracesCommand(["ingest"], { cwd });
}

beforeEach(() => {
  cwd = mkdtempSync(join(tmpdir(), "autocontext-pt-cli-export-"));
});
afterEach(() => {
  rmSync(cwd, { recursive: true, force: true });
});

describe("autoctx production-traces export", () => {
  test("--format parquet returns exit 1 with deferral message", async () => {
    await runProductionTracesCommand(["init"], { cwd });
    await seed(1);
    const r = await runProductionTracesCommand(
      ["export", "--format", "parquet"],
      { cwd },
    );
    expect(r.exitCode).toBe(1);
    expect(r.stderr.toLowerCase()).toContain("parquet");
  });

  test("--format jsonl writes stdout as one-trace-per-line JSONL", async () => {
    await runProductionTracesCommand(["init"], { cwd });
    await seed(2);
    const r = await runProductionTracesCommand(
      ["export", "--format", "jsonl"],
      { cwd },
    );
    expect(r.exitCode).toBe(0);
    const lines = r.stdout.trim().split("\n");
    expect(lines.length).toBe(2);
    for (const ln of lines) {
      const obj = JSON.parse(ln);
      expect(typeof obj.traceId).toBe("string");
    }
  });

  test("--format public-trace emits a single JSON array", async () => {
    await runProductionTracesCommand(["init"], { cwd });
    await seed(2);
    const r = await runProductionTracesCommand(
      ["export", "--format", "public-trace"],
      { cwd },
    );
    expect(r.exitCode).toBe(0);
    const parsed = JSON.parse(r.stdout);
    expect(Array.isArray(parsed)).toBe(true);
    expect(parsed).toHaveLength(2);
  });

  test("--output-path writes to disk and returns summary", async () => {
    await runProductionTracesCommand(["init"], { cwd });
    await seed(1);
    const out = join(cwd, "exports", "out.jsonl");
    const r = await runProductionTracesCommand(
      [
        "export",
        "--format",
        "jsonl",
        "--output-path",
        out,
        "--output",
        "json",
      ],
      { cwd },
    );
    expect(r.exitCode).toBe(0);
    expect(existsSync(out)).toBe(true);
    const summary = JSON.parse(r.stdout);
    expect(summary.tracesExported).toBe(1);
    const body = readFileSync(out, "utf-8");
    expect(body.trim().split("\n")).toHaveLength(1);
  });

  test("empty ingested traces yields exit 12", async () => {
    await runProductionTracesCommand(["init"], { cwd });
    const r = await runProductionTracesCommand(
      ["export", "--format", "jsonl"],
      { cwd },
    );
    expect(r.exitCode).toBe(12);
  });

  test("invalid --category-override rejected with exit 1", async () => {
    await runProductionTracesCommand(["init"], { cwd });
    await seed(1);
    const r = await runProductionTracesCommand(
      ["export", "--format", "jsonl", "--category-override", "pii-email=fake-action"],
      { cwd },
    );
    expect(r.exitCode).toBe(1);
    expect(r.stderr.toLowerCase()).toContain("category-override");
  });

  test("--help exits 0", async () => {
    const r = await runProductionTracesCommand(["export", "--help"], { cwd });
    expect(r.exitCode).toBe(0);
  });
});
