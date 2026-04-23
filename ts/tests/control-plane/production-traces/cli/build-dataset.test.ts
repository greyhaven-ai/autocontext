import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, existsSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runProductionTracesCommand } from "../../../../src/production-traces/cli/index.js";
import { makeTrace, writeIncomingBatch, TEST_DATE } from "./_helpers/fixtures.js";
import { newProductionTraceId } from "../../../../src/production-traces/contract/branded-ids.js";

let cwd: string;

beforeEach(() => {
  cwd = mkdtempSync(join(tmpdir(), "autocontext-pt-cli-build-"));
});
afterEach(() => {
  rmSync(cwd, { recursive: true, force: true });
});

async function seedTraces(n: number, taskType = "checkout"): Promise<void> {
  const base = Date.parse("2026-04-17T12:00:00.000Z");
  const traces = Array.from({ length: n }, (_, i) =>
    makeTrace({
      traceId: newProductionTraceId(),
      startedAt: new Date(base + i * 60_000).toISOString(),
      env: {
        environmentTag: "production" as any,
        appId: "my-app" as any,
        taskType,
      },
      outcome: { label: "success", score: 0.9 },
    }),
  );
  writeIncomingBatch(cwd, TEST_DATE, "batch-bd", traces);
  await runProductionTracesCommand(["ingest"], { cwd });
}

describe("autoctx production-traces build-dataset", () => {
  test("end-to-end: taskType strategy + allow-synthetic-rubrics → dataset written", async () => {
    await runProductionTracesCommand(["init"], { cwd });
    await seedTraces(4);

    const r = await runProductionTracesCommand(
      [
        "build-dataset",
        "--name",
        "my-dataset",
        "--cluster-strategy",
        "taskType",
        "--allow-synthetic-rubrics",
        "--output",
        "json",
      ],
      { cwd },
    );
    expect(r.exitCode).toBe(0);
    const result = JSON.parse(r.stdout);
    expect(typeof result.datasetId).toBe("string");
    expect(result.datasetId.startsWith("ds_")).toBe(true);
    expect(existsSync(join(result.writePath, "manifest.json"))).toBe(true);
    expect(existsSync(join(result.writePath, "train.jsonl"))).toBe(true);
  });

  test("no matching traces yields exit 12", async () => {
    await runProductionTracesCommand(["init"], { cwd });
    const r = await runProductionTracesCommand(
      [
        "build-dataset",
        "--name",
        "empty",
        "--cluster-strategy",
        "taskType",
        "--allow-synthetic-rubrics",
        "--output",
        "json",
      ],
      { cwd },
    );
    expect(r.exitCode).toBe(12);
  });

  test("missing --name is a required-flag error", async () => {
    const r = await runProductionTracesCommand(
      ["build-dataset", "--cluster-strategy", "taskType"],
      { cwd },
    );
    expect(r.exitCode).toBe(1);
    expect(r.stderr).toContain("--name");
  });

  test("--cluster-strategy rules without --rules yields exit 1", async () => {
    await runProductionTracesCommand(["init"], { cwd });
    await seedTraces(1);
    const r = await runProductionTracesCommand(
      [
        "build-dataset",
        "--name",
        "needs-rules",
        "--cluster-strategy",
        "rules",
      ],
      { cwd },
    );
    expect(r.exitCode).toBe(1);
  });

  test("invalid --rules path yields exit 11 (invalid config)", async () => {
    await runProductionTracesCommand(["init"], { cwd });
    await seedTraces(1);
    const r = await runProductionTracesCommand(
      [
        "build-dataset",
        "--name",
        "bad-rules",
        "--cluster-strategy",
        "rules",
        "--rules",
        join(cwd, "nonexistent.json"),
      ],
      { cwd },
    );
    expect(r.exitCode).toBe(11);
  });

  test("malformed --rules JSON yields exit 11", async () => {
    await runProductionTracesCommand(["init"], { cwd });
    await seedTraces(1);
    const rulesPath = join(cwd, "rules.json");
    writeFileSync(rulesPath, "{ not json ");
    const r = await runProductionTracesCommand(
      [
        "build-dataset",
        "--name",
        "bad-rules-json",
        "--cluster-strategy",
        "rules",
        "--rules",
        rulesPath,
      ],
      { cwd },
    );
    expect(r.exitCode).toBe(11);
  });

  test("--new-id produces a ds_* dataset id", async () => {
    await runProductionTracesCommand(["init"], { cwd });
    await seedTraces(2);
    const r = await runProductionTracesCommand(
      [
        "build-dataset",
        "--name",
        "with-new-id",
        "--cluster-strategy",
        "taskType",
        "--allow-synthetic-rubrics",
        "--new-id",
        "--output",
        "json",
      ],
      { cwd },
    );
    expect(r.exitCode).toBe(0);
    const result = JSON.parse(r.stdout);
    expect(result.datasetId).toMatch(/^ds_[0-9A-HJKMNP-TV-Z]{26}$/);
  });

  test("--help exits 0", async () => {
    const r = await runProductionTracesCommand(
      ["build-dataset", "--help"],
      { cwd },
    );
    expect(r.exitCode).toBe(0);
  });

  test("makeTrace accepts provider override", () => {
    const t = makeTrace({ provider: { name: "anthropic" } });
    expect(t.provider.name).toBe("anthropic");
  });

  describe("--provider filter", () => {
    test("--provider anthropic returns only anthropic traces", async () => {
      await runProductionTracesCommand(["init"], { cwd });

      // Seed 2 openai + 2 anthropic traces
      const base = Date.parse("2026-04-17T12:00:00.000Z");
      const openaiTraces = Array.from({ length: 2 }, (_, i) =>
        makeTrace({
          traceId: newProductionTraceId(),
          startedAt: new Date(base + i * 60_000).toISOString(),
          provider: { name: "openai" },
          env: { environmentTag: "production" as any, appId: "app1" as any, taskType: "chat" },
          outcome: { label: "success" },
        }),
      );
      const anthropicTraces = Array.from({ length: 2 }, (_, i) =>
        makeTrace({
          traceId: newProductionTraceId(),
          startedAt: new Date(base + (i + 2) * 60_000).toISOString(),
          provider: { name: "anthropic" },
          env: { environmentTag: "production" as any, appId: "app1" as any, taskType: "chat" },
          outcome: { label: "success" },
        }),
      );
      writeIncomingBatch(cwd, TEST_DATE, "batch-openai", openaiTraces);
      writeIncomingBatch(cwd, TEST_DATE, "batch-anthropic", anthropicTraces);
      await runProductionTracesCommand(["ingest"], { cwd });

      const r = await runProductionTracesCommand(
        [
          "build-dataset",
          "--name",
          "anthropic-only",
          "--provider",
          "anthropic",
          "--cluster-strategy",
          "taskType",
          "--allow-synthetic-rubrics",
          "--output",
          "json",
        ],
        { cwd },
      );
      expect(r.exitCode).toBe(0);
      const result = JSON.parse(r.stdout);
      // 2 anthropic traces → 1 cluster → split produces at least 1 train row
      expect(result.stats.traceCount).toBe(2);
    });

    test("--provider openai with only anthropic traces yields exit 12", async () => {
      await runProductionTracesCommand(["init"], { cwd });

      const base = Date.parse("2026-04-17T12:00:00.000Z");
      const anthropicTraces = Array.from({ length: 2 }, (_, i) =>
        makeTrace({
          traceId: newProductionTraceId(),
          startedAt: new Date(base + i * 60_000).toISOString(),
          provider: { name: "anthropic" },
          env: { environmentTag: "production" as any, appId: "app1" as any, taskType: "chat" },
          outcome: { label: "success" },
        }),
      );
      writeIncomingBatch(cwd, TEST_DATE, "batch-anth", anthropicTraces);
      await runProductionTracesCommand(["ingest"], { cwd });

      const r = await runProductionTracesCommand(
        [
          "build-dataset",
          "--name",
          "openai-only",
          "--provider",
          "openai",
          "--cluster-strategy",
          "taskType",
          "--allow-synthetic-rubrics",
        ],
        { cwd },
      );
      expect(r.exitCode).toBe(12);
    });
  });

  describe("--app / --env / --outcome filters", () => {
    async function seedMixedTraces(): Promise<void> {
      const base = Date.parse("2026-04-17T12:00:00.000Z");
      const traces = [
        makeTrace({
          traceId: newProductionTraceId(),
          startedAt: new Date(base).toISOString(),
          env: { environmentTag: "production" as any, appId: "app-alpha" as any, taskType: "chat" },
          outcome: { label: "success" },
        }),
        makeTrace({
          traceId: newProductionTraceId(),
          startedAt: new Date(base + 60_000).toISOString(),
          env: { environmentTag: "staging" as any, appId: "app-beta" as any, taskType: "chat" },
          outcome: { label: "failure" },
        }),
        makeTrace({
          traceId: newProductionTraceId(),
          startedAt: new Date(base + 120_000).toISOString(),
          env: { environmentTag: "production" as any, appId: "app-alpha" as any, taskType: "chat" },
          outcome: { label: "success" },
        }),
      ];
      writeIncomingBatch(cwd, TEST_DATE, "batch-mixed", traces);
      await runProductionTracesCommand(["ingest"], { cwd });
    }

    test("--app filters to matching appId only", async () => {
      await runProductionTracesCommand(["init"], { cwd });
      await seedMixedTraces();

      const r = await runProductionTracesCommand(
        [
          "build-dataset",
          "--name",
          "app-alpha-ds",
          "--app",
          "app-alpha",
          "--cluster-strategy",
          "taskType",
          "--allow-synthetic-rubrics",
          "--output",
          "json",
        ],
        { cwd },
      );
      expect(r.exitCode).toBe(0);
      expect(JSON.parse(r.stdout).stats.traceCount).toBe(2);
    });

    test("--env filters to matching environmentTag only", async () => {
      await runProductionTracesCommand(["init"], { cwd });
      await seedMixedTraces();

      const r = await runProductionTracesCommand(
        [
          "build-dataset",
          "--name",
          "prod-ds",
          "--env",
          "production",
          "--cluster-strategy",
          "taskType",
          "--allow-synthetic-rubrics",
          "--output",
          "json",
        ],
        { cwd },
      );
      expect(r.exitCode).toBe(0);
      expect(JSON.parse(r.stdout).stats.traceCount).toBe(2);
    });

    test("--outcome filters to matching outcome label only", async () => {
      await runProductionTracesCommand(["init"], { cwd });
      await seedMixedTraces();

      const r = await runProductionTracesCommand(
        [
          "build-dataset",
          "--name",
          "success-only-ds",
          "--outcome",
          "success",
          "--cluster-strategy",
          "taskType",
          "--allow-synthetic-rubrics",
          "--output",
          "json",
        ],
        { cwd },
      );
      expect(r.exitCode).toBe(0);
      expect(JSON.parse(r.stdout).stats.traceCount).toBe(2);
    });

    test("combined filters: --app + --outcome", async () => {
      await runProductionTracesCommand(["init"], { cwd });
      await seedMixedTraces();

      // app-beta only has 1 trace but it's outcome=failure; filtering success should give 0 → exit 12
      const r = await runProductionTracesCommand(
        [
          "build-dataset",
          "--name",
          "beta-success-ds",
          "--app",
          "app-beta",
          "--outcome",
          "success",
          "--cluster-strategy",
          "taskType",
          "--allow-synthetic-rubrics",
        ],
        { cwd },
      );
      expect(r.exitCode).toBe(12);
    });
  });
});
