import { describe, test, expect, beforeEach } from "vitest";
import { mkdtempSync, existsSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { buildDataset } from "../../../../src/production-traces/dataset/pipeline.js";
import { validateDatasetManifest, validateDatasetRow } from "../../../../src/production-traces/contract/validators.js";
import { makeTrace, MINIMAL_POLICY } from "./_helpers/fixtures.js";
import type {
  BuildDatasetInputs,
  Rubric,
  SelectionRule,
} from "../../../../src/production-traces/dataset/types.js";
import type { ProductionTrace } from "../../../../src/production-traces/contract/types.js";

function baseInputs(overrides: Partial<BuildDatasetInputs> = {}): BuildDatasetInputs {
  const cwd = mkdtempSync(join(tmpdir(), "pipeline-"));
  const rubric: Rubric = { rubricId: "default-rubric", dimensions: ["accuracy"] };
  return {
    cwd,
    name: "demo",
    description: "a demo dataset",
    traces: [],
    clusterStrategy: "taskType",
    selectionRules: [],
    rubricConfig: {
      rubricsByCluster: {
        x: { source: "inline", rubric },
        uncategorized: { source: "inline", rubric },
        checkout: { source: "inline", rubric },
        other: { source: "inline", rubric },
      },
    },
    allowSyntheticRubrics: false,
    redactionPolicy: MINIMAL_POLICY,
    installSalt: null,
    seed: 42,
    autoctxVersion: "0.4.3-test",
    ...overrides,
  };
}

describe("buildDataset end-to-end", () => {
  test("empty traces → skippedClusters=0, empty splits, manifest still valid", async () => {
    const inputs = baseInputs({ traces: [] });
    const res = await buildDataset(inputs);
    expect(res.stats.traceCount).toBe(0);
    expect(res.stats.clusterCount).toBe(0);
    expect(res.stats.splitSizes.train).toBe(0);
    const r = validateDatasetManifest(res.manifest);
    if (!r.valid) throw new Error(r.errors.join("; "));
  });

  test("simple taskType clustering + single-cluster rubric", async () => {
    const traces: ProductionTrace[] = [
      makeTrace({ traceId: "01K00000000000000000000001", taskType: "x" }),
      makeTrace({ traceId: "01K00000000000000000000002", taskType: "x" }),
    ];
    const inputs = baseInputs({ traces });
    const res = await buildDataset(inputs);
    expect(res.stats.traceCount).toBe(2);
    expect(res.stats.clusterCount).toBe(1);
    expect(res.stats.splitSizes.train).toBe(2);
    expect(res.datasetId).toMatch(/^ds_[0-9A-HJKMNP-TV-Z]{26}$/);
  });

  test("output directory layout matches spec §8.4", async () => {
    const traces = [
      makeTrace({ traceId: "01K00000000000000000000001", taskType: "x" }),
    ];
    const res = await buildDataset(baseInputs({ traces }));
    const dirEntries = readdirSync(res.writePath).sort();
    expect(dirEntries).toContain("manifest.json");
    expect(dirEntries).toContain("train.jsonl");
    expect(dirEntries).toContain("eval.jsonl");
    expect(dirEntries).toContain("holdout.jsonl");
    expect(dirEntries).toContain("cluster-stats.json");
    expect(dirEntries).toContain("rubrics");
    expect(existsSync(join(res.writePath, "rubrics", "default-rubric.json"))).toBe(true);
  });

  test("each JSONL row validates against dataset-row schema", async () => {
    const traces = [
      makeTrace({ traceId: "01K00000000000000000000001", taskType: "x" }),
      makeTrace({ traceId: "01K00000000000000000000002", taskType: "x" }),
    ];
    const res = await buildDataset(baseInputs({ traces }));
    const content = readFileSync(join(res.writePath, "train.jsonl"), "utf-8");
    const lines = content.trim().split("\n");
    expect(lines.length).toBeGreaterThan(0);
    for (const line of lines) {
      const parsed = JSON.parse(line);
      const r = validateDatasetRow(parsed);
      if (!r.valid) throw new Error(`row failed: ${r.errors.join("; ")}\n${line}`);
    }
  });

  test("clusters without a rubric are skipped + recorded", async () => {
    const traces = [
      makeTrace({ traceId: "01K00000000000000000000001", taskType: "unconfigured" }),
    ];
    const inputs = baseInputs({
      traces,
      rubricConfig: { rubricsByCluster: {} }, // no rubric for any cluster
    });
    const res = await buildDataset(inputs);
    expect(res.stats.clustersSkipped).toBe(1);
    const skipped = res.manifest.clusters.find((c) => c.clusterId === "unconfigured");
    expect(skipped?.skippedReason).toBeDefined();
  });

  test("split rule produces train/eval/holdout partitions", async () => {
    const traces = Array.from({ length: 10 }, (_, i) =>
      makeTrace({
        traceId: `01K0000000000000000000000${i.toString(16).toUpperCase()}`,
        taskType: "x",
      }),
    );
    const rules: SelectionRule[] = [
      { type: "split", train: 0.6, eval: 0.2, holdout: 0.2, shuffle: false, seed: 7 },
    ];
    const res = await buildDataset(baseInputs({ traces, selectionRules: rules }));
    expect(res.stats.splitSizes.train + res.stats.splitSizes.eval + res.stats.splitSizes.holdout).toBe(10);
    expect(res.stats.splitSizes.train).toBe(6);
    expect(res.stats.splitSizes.eval).toBe(2);
    expect(res.stats.splitSizes.holdout).toBe(2);
  });

  test("gate rule filters traces before rubric resolution", async () => {
    const traces = [
      makeTrace({ traceId: "01K00000000000000000000001", taskType: "keep" }),
      makeTrace({ traceId: "01K00000000000000000000002", taskType: "drop" }),
    ];
    const rubric: Rubric = { rubricId: "r", dimensions: ["a"] };
    const rules: SelectionRule[] = [
      { type: "gate", include: [{ "env.taskType": { equals: "keep" } }] },
    ];
    const res = await buildDataset(baseInputs({
      traces,
      selectionRules: rules,
      rubricConfig: {
        rubricsByCluster: {
          keep: { source: "inline", rubric },
          drop: { source: "inline", rubric },
        },
      },
    }));
    // `drop` cluster was present at cluster time but had zero rows after gate
    // → either skipped with "no traces retained" or absent from included list.
    const kept = res.manifest.clusters.find((c) => c.clusterId === "keep");
    expect(kept?.size).toBe(1);
  });

  test("--new-id produces fresh time-ordered ULID", async () => {
    const traces = [
      makeTrace({ traceId: "01K00000000000000000000001", taskType: "x" }),
    ];
    const r1 = await buildDataset(baseInputs({ traces, newId: true }));
    const r2 = await buildDataset(baseInputs({ traces, newId: true }));
    expect(r1.datasetId).not.toBe(r2.datasetId);
    expect(r1.datasetId).toMatch(/^ds_[0-9A-HJKMNP-TV-Z]{26}$/);
  });

  test("content-addressed id is stable across invocations (same config + traces)", async () => {
    const traces = [
      makeTrace({ traceId: "01K00000000000000000000001", taskType: "x" }),
    ];
    const inputs = baseInputs({ traces });
    const r1 = await buildDataset(inputs);
    // Re-run with same logical inputs but fresh cwd (shouldn't affect datasetId).
    const inputs2 = baseInputs({ traces });
    const r2 = await buildDataset(inputs2);
    expect(r1.datasetId).toBe(r2.datasetId);
  });
});

describe("clusterStrategy: rules", () => {
  test("routes traces per rule-based cluster config", async () => {
    const cartTrace = makeTrace({
      traceId: "01K00000000000000000000001",
      messages: [{ role: "user", content: "checkout my cart", timestamp: "2026-04-17T12:00:00.000Z" }],
    });
    const otherTrace = makeTrace({ traceId: "01K00000000000000000000002" });
    const rubric: Rubric = { rubricId: "r", dimensions: ["a"] };
    const res = await buildDataset(baseInputs({
      traces: [cartTrace, otherTrace],
      clusterStrategy: "rules",
      clusterConfig: {
        strategy: "rules",
        rules: [
          { id: "checkout", match: { "messages[0].content": { contains: "cart" } } },
          { id: "other", match: { default: { default: true } } },
        ],
      },
      rubricConfig: {
        rubricsByCluster: {
          checkout: { source: "inline", rubric },
          other: { source: "inline", rubric },
        },
      },
    }));
    const checkout = res.manifest.clusters.find((c) => c.clusterId === "checkout");
    const other = res.manifest.clusters.find((c) => c.clusterId === "other");
    expect(checkout?.size).toBe(1);
    expect(other?.size).toBe(1);
  });
});
