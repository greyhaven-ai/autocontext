import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runProductionTracesCommand } from "../../../../src/production-traces/cli/index.js";
import { makeTrace, writeIncomingBatch, TEST_DATE } from "./_helpers/fixtures.js";
import { newProductionTraceId } from "../../../../src/production-traces/contract/branded-ids.js";

let cwd: string;

async function seedIngested(batchTag: string, count = 3): Promise<void> {
  const traces = Array.from({ length: count }, (_, i) =>
    makeTrace({
      traceId: newProductionTraceId(),
      startedAt: new Date(Date.parse("2026-04-17T12:00:00.000Z") + i * 60_000).toISOString(),
      env: {
        environmentTag: i === 0 ? "production" : ("staging" as any),
        appId: "my-app" as any,
        taskType: i === 0 ? "checkout" : "search",
      },
      outcome: i === 0 ? { label: "success", score: 0.9 } : { label: "failure", score: 0.2 },
    }),
  );
  writeIncomingBatch(cwd, TEST_DATE, batchTag, traces);
  const r = await runProductionTracesCommand(["ingest", "--output", "json"], { cwd });
  if (r.exitCode !== 0) {
    throw new Error(`seedIngested: ingest failed: exit=${r.exitCode} stderr=${r.stderr}`);
  }
}

beforeEach(() => {
  cwd = mkdtempSync(join(tmpdir(), "autocontext-pt-cli-list-"));
});
afterEach(() => {
  rmSync(cwd, { recursive: true, force: true });
});

describe("autoctx production-traces list", () => {
  test("lists ingested traces with no filter; --output json is parseable", async () => {
    await seedIngested("batch-list", 3);
    const r = await runProductionTracesCommand(["list", "--output", "json"], { cwd });
    expect(r.exitCode).toBe(0);
    const rows = JSON.parse(r.stdout);
    expect(Array.isArray(rows)).toBe(true);
    expect(rows).toHaveLength(3);
    for (const row of rows) {
      expect(typeof row.traceId).toBe("string");
      expect(typeof row.startedAt).toBe("string");
    }
  });

  test("--env filter narrows the rows", async () => {
    await seedIngested("batch-env", 3);
    const r = await runProductionTracesCommand(
      ["list", "--env", "production", "--output", "json"],
      { cwd },
    );
    expect(r.exitCode).toBe(0);
    const rows = JSON.parse(r.stdout);
    expect(rows).toHaveLength(1);
    expect(rows[0].env).toBe("production");
  });

  test("--limit caps rows", async () => {
    await seedIngested("batch-limit", 3);
    const r = await runProductionTracesCommand(
      ["list", "--limit", "2", "--output", "json"],
      { cwd },
    );
    expect(r.exitCode).toBe(0);
    expect(JSON.parse(r.stdout)).toHaveLength(2);
  });

  test("--limit must be a positive integer", async () => {
    const r = await runProductionTracesCommand(
      ["list", "--limit", "abc"],
      { cwd },
    );
    expect(r.exitCode).toBe(1);
  });

  test("empty ingested/ returns []", async () => {
    const r = await runProductionTracesCommand(["list", "--output", "json"], { cwd });
    expect(r.exitCode).toBe(0);
    expect(JSON.parse(r.stdout)).toEqual([]);
  });
});

describe("autoctx production-traces show", () => {
  test("shows a known trace by id", async () => {
    const id = newProductionTraceId();
    writeIncomingBatch(cwd, TEST_DATE, "batch-show", [makeTrace({ traceId: id })]);
    await runProductionTracesCommand(["ingest"], { cwd });

    const r = await runProductionTracesCommand(
      ["show", id, "--output", "json"],
      { cwd },
    );
    expect(r.exitCode).toBe(0);
    const trace = JSON.parse(r.stdout);
    expect(trace.traceId).toBe(id);
  });

  test("unknown trace id returns exit 12 (no matching traces)", async () => {
    const id = newProductionTraceId();
    const r = await runProductionTracesCommand([
      "show",
      id,
      "--output",
      "json",
    ], { cwd });
    expect(r.exitCode).toBe(12);
    expect(r.stderr).toContain(id);
  });

  test("--as-exported applies redaction (still returns the trace)", async () => {
    // Init first so the install-salt + redaction-policy exist.
    await runProductionTracesCommand(["init"], { cwd });
    const id = newProductionTraceId();
    writeIncomingBatch(cwd, TEST_DATE, "batch-show-ae", [makeTrace({ traceId: id })]);
    await runProductionTracesCommand(["ingest"], { cwd });

    const r = await runProductionTracesCommand(
      ["show", id, "--as-exported", "--output", "json"],
      { cwd },
    );
    expect(r.exitCode).toBe(0);
    const trace = JSON.parse(r.stdout);
    expect(trace.traceId).toBe(id);
  });

  test("no args shows help + exit 0", async () => {
    const r = await runProductionTracesCommand(["show"], { cwd });
    expect(r.exitCode).toBe(0);
  });
});

describe("autoctx production-traces stats", () => {
  test("default --by env returns grouped counts", async () => {
    await seedIngested("batch-stats", 3);
    const r = await runProductionTracesCommand(["stats", "--output", "json"], { cwd });
    expect(r.exitCode).toBe(0);
    const rows = JSON.parse(r.stdout);
    const byEnv = new Map<string, number>(rows.map((r: any) => [r.env, r.count]));
    expect(byEnv.get("production")).toBe(1);
    expect(byEnv.get("staging")).toBe(2);
  });

  test("--by outcome groups by outcome label", async () => {
    await seedIngested("batch-stats-out", 3);
    const r = await runProductionTracesCommand(
      ["stats", "--by", "outcome", "--output", "json"],
      { cwd },
    );
    expect(r.exitCode).toBe(0);
    const rows = JSON.parse(r.stdout);
    const byOut = new Map<string, number>(rows.map((r: any) => [r.outcome, r.count]));
    expect(byOut.get("success")).toBe(1);
    expect(byOut.get("failure")).toBe(2);
  });

  test("--by cluster groups by env.taskType (Tier-1 clustering)", async () => {
    await seedIngested("batch-stats-cluster", 3);
    const r = await runProductionTracesCommand(
      ["stats", "--by", "cluster", "--output", "json"],
      { cwd },
    );
    expect(r.exitCode).toBe(0);
    const rows = JSON.parse(r.stdout);
    const byCluster = new Map<string, number>(rows.map((r: any) => [r.cluster, r.count]));
    expect(byCluster.get("checkout")).toBe(1);
    expect(byCluster.get("search")).toBe(2);
  });

  test("unknown --by value returns exit 1", async () => {
    const r = await runProductionTracesCommand(
      ["stats", "--by", "unknown-axis"],
      { cwd },
    );
    expect(r.exitCode).toBe(1);
  });
});
