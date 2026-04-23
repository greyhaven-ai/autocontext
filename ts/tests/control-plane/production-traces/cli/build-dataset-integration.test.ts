/**
 * E2E pipeline integration: mixed-provider traces (openai + anthropic)
 * flow through ingest → build-dataset with provider-scoped filtering.
 *
 * This validates the full AC-606 contract: traces produced by
 * instrument_client (OpenAI / Anthropic) serialize to the ProductionTrace
 * schema via FileSink → incoming/ → ingest → ingested/ → build-dataset.
 *
 * We use makeTrace with provider overrides because the serialized shape is
 * identical to what FileSink writes — no live HTTP mock needed.
 */
import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, existsSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runProductionTracesCommand } from "../../../../src/production-traces/cli/index.js";
import { makeTrace, writeIncomingBatch, TEST_DATE } from "./_helpers/fixtures.js";
import { newProductionTraceId } from "../../../../src/production-traces/contract/branded-ids.js";

let cwd: string;

beforeEach(() => {
  cwd = mkdtempSync(join(tmpdir(), "autocontext-pt-integration-"));
});
afterEach(() => {
  rmSync(cwd, { recursive: true, force: true });
});

describe("AC-606: OpenAI + Anthropic traces through ingest → build-dataset", () => {
  async function seedProviderTraces(): Promise<void> {
    const base = Date.parse("2026-04-17T12:00:00.000Z");

    const openaiTraces = Array.from({ length: 3 }, (_, i) =>
      makeTrace({
        traceId: newProductionTraceId(),
        startedAt: new Date(base + i * 60_000).toISOString(),
        provider: { name: "openai" },
        env: { environmentTag: "production" as any, appId: "my-app" as any, taskType: "customer-support" },
        outcome: { label: "success" },
      }),
    );

    const anthropicTraces = Array.from({ length: 3 }, (_, i) =>
      makeTrace({
        traceId: newProductionTraceId(),
        startedAt: new Date(base + (i + 3) * 60_000).toISOString(),
        provider: { name: "anthropic" },
        env: { environmentTag: "production" as any, appId: "my-app" as any, taskType: "customer-support" },
        outcome: { label: "success" },
      }),
    );

    writeIncomingBatch(cwd, TEST_DATE, "openai-batch", openaiTraces);
    writeIncomingBatch(cwd, TEST_DATE, "anthropic-batch", anthropicTraces);

    const ingestResult = await runProductionTracesCommand(["ingest"], { cwd });
    expect(ingestResult.exitCode).toBe(0);
  }

  test("all 6 traces ingest successfully", async () => {
    await runProductionTracesCommand(["init"], { cwd });
    await seedProviderTraces();

    // `stats --output json` returns an array of grouped rows, not {totalTraces}.
    // Use `list --output json` to get all trace rows and count them.
    const listResult = await runProductionTracesCommand(
      ["list", "--output", "json"],
      { cwd },
    );
    expect(listResult.exitCode).toBe(0);
    const rows = JSON.parse(listResult.stdout);
    expect(Array.isArray(rows)).toBe(true);
    expect(rows).toHaveLength(6);
  });

  test("build-dataset with --provider openai includes only openai traces", async () => {
    await runProductionTracesCommand(["init"], { cwd });
    await seedProviderTraces();

    const result = await runProductionTracesCommand(
      [
        "build-dataset",
        "--name", "openai-dataset",
        "--provider", "openai",
        "--cluster-strategy", "taskType",
        "--allow-synthetic-rubrics",
        "--output", "json",
      ],
      { cwd },
    );
    expect(result.exitCode).toBe(0);
    const ds = JSON.parse(result.stdout);
    expect(ds.stats.traceCount).toBe(3);
    expect(existsSync(join(ds.writePath, "manifest.json"))).toBe(true);
    expect(existsSync(join(ds.writePath, "train.jsonl"))).toBe(true);
    const manifest = JSON.parse(readFileSync(join(ds.writePath, "manifest.json"), "utf-8"));
    expect(manifest.source.traceCount).toBe(3);
  });

  test("build-dataset with --provider anthropic includes only anthropic traces", async () => {
    await runProductionTracesCommand(["init"], { cwd });
    await seedProviderTraces();

    const result = await runProductionTracesCommand(
      [
        "build-dataset",
        "--name", "anthropic-dataset",
        "--provider", "anthropic",
        "--cluster-strategy", "taskType",
        "--allow-synthetic-rubrics",
        "--output", "json",
      ],
      { cwd },
    );
    expect(result.exitCode).toBe(0);
    const ds = JSON.parse(result.stdout);
    expect(ds.stats.traceCount).toBe(3);
    expect(existsSync(join(ds.writePath, "manifest.json"))).toBe(true);
    expect(existsSync(join(ds.writePath, "train.jsonl"))).toBe(true);
    const manifest = JSON.parse(readFileSync(join(ds.writePath, "manifest.json"), "utf-8"));
    expect(manifest.source.traceCount).toBe(3);
  });

  test("build-dataset without --provider includes all 6 traces", async () => {
    await runProductionTracesCommand(["init"], { cwd });
    await seedProviderTraces();

    const result = await runProductionTracesCommand(
      [
        "build-dataset",
        "--name", "all-providers-dataset",
        "--cluster-strategy", "taskType",
        "--allow-synthetic-rubrics",
        "--output", "json",
      ],
      { cwd },
    );
    expect(result.exitCode).toBe(0);
    const ds = JSON.parse(result.stdout);
    expect(ds.stats.traceCount).toBe(6);
  });

  test("two separate per-provider datasets have non-overlapping trace sets", async () => {
    await runProductionTracesCommand(["init"], { cwd });
    await seedProviderTraces();

    const [openaiResult, anthropicResult] = await Promise.all([
      runProductionTracesCommand(
        ["build-dataset", "--name", "openai-ds", "--provider", "openai",
         "--cluster-strategy", "taskType", "--allow-synthetic-rubrics", "--output", "json"],
        { cwd },
      ),
      runProductionTracesCommand(
        ["build-dataset", "--name", "anthropic-ds", "--provider", "anthropic",
         "--cluster-strategy", "taskType", "--allow-synthetic-rubrics", "--output", "json"],
        { cwd },
      ),
    ]);

    expect(openaiResult.exitCode).toBe(0);
    expect(anthropicResult.exitCode).toBe(0);

    const openaiDs = JSON.parse(openaiResult.stdout);
    const anthropicDs = JSON.parse(anthropicResult.stdout);

    expect(openaiDs.datasetId).not.toBe(anthropicDs.datasetId);

    // DatasetRow.source.traceIds is an array of ProductionTraceId
    const readTraceIds = (dsPath: string): Set<string> => {
      const trainPath = join(dsPath, "train.jsonl");
      if (!existsSync(trainPath)) return new Set();
      const lines = readFileSync(trainPath, "utf-8").trim().split("\n").filter(Boolean);
      const ids = new Set<string>();
      for (const l of lines) {
        const row = JSON.parse(l) as { source?: { traceIds?: string[] } };
        for (const id of row.source?.traceIds ?? []) ids.add(id);
      }
      return ids;
    };

    const openaiIds = readTraceIds(openaiDs.writePath);
    const anthropicIds = readTraceIds(anthropicDs.writePath);
    expect(openaiIds.size).toBeGreaterThan(0);
    expect(anthropicIds.size).toBeGreaterThan(0);
    const intersection = [...openaiIds].filter((id) => anthropicIds.has(id));
    expect(intersection).toHaveLength(0);
  });
});
