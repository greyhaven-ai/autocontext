/**
 * Property test P1: Dataset determinism.
 *
 * Same {config, sourceTraces} → same datasetId → byte-identical JSONL + manifest
 * files. This is the top-level idempotence guarantee. Spec §10.1 requires 100
 * runs; we run with varied input shapes each time.
 */
import { describe, test, expect } from "vitest";
import fc from "fast-check";
import { mkdtempSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { buildDataset } from "../../../../src/production-traces/dataset/pipeline.js";
import { makeTrace, MINIMAL_POLICY } from "./_helpers/fixtures.js";
import type {
  BuildDatasetInputs,
  Rubric,
  SelectionRule,
} from "../../../../src/production-traces/dataset/types.js";
import type { ProductionTrace } from "../../../../src/production-traces/contract/types.js";

const RUBRIC: Rubric = { rubricId: "default-rubric", dimensions: ["a"] };

function inputsWith(traces: readonly ProductionTrace[], rules: readonly SelectionRule[], seed: number): BuildDatasetInputs {
  return {
    cwd: mkdtempSync(join(tmpdir(), "p1-")),
    name: "p1-test",
    description: "",
    traces,
    clusterStrategy: "taskType",
    selectionRules: rules,
    rubricConfig: {
      rubricsByCluster: {
        x: { source: "inline", rubric: RUBRIC },
        y: { source: "inline", rubric: RUBRIC },
        z: { source: "inline", rubric: RUBRIC },
        uncategorized: { source: "inline", rubric: RUBRIC },
      },
    },
    allowSyntheticRubrics: false,
    redactionPolicy: MINIMAL_POLICY,
    installSalt: null,
    seed,
    autoctxVersion: "0.4.3-test",
  };
}

/**
 * Generate a small deterministic batch of traces with stable ULIDs so the
 * property test is reproducible on failure.
 */
function traceBatch(count: number, taskTypes: readonly string[]): ProductionTrace[] {
  const out: ProductionTrace[] = [];
  for (let i = 0; i < count; i += 1) {
    // Produce ULIDs of the shape 01K0000...<4-hex-digits> — within Crockford base32.
    const suffix = i.toString(16).toUpperCase().padStart(4, "0").replace(/[ILOU]/g, "0");
    const traceId = `01K00000000000000000000${suffix}`.slice(0, 26);
    const taskType = taskTypes[i % taskTypes.length];
    out.push(makeTrace({
      traceId,
      taskType,
      startedAt: new Date(Date.parse("2026-04-17T12:00:00.000Z") + i * 1000).toISOString(),
    }));
  }
  return out;
}

describe("P1: same inputs + same config + same seed → byte-identical output", () => {
  test("deterministic datasetId + deterministic file contents (100 runs)", async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.integer({ min: 1, max: 10 }),
        fc.constantFrom(["x"], ["x", "y"], ["x", "y", "z"]),
        fc.integer({ min: 0, max: 2 ** 30 - 1 }),
        async (traceCount, taskTypes, seed) => {
          const traces = traceBatch(traceCount, taskTypes);
          const rules: SelectionRule[] = [
            { type: "split", train: 0.7, eval: 0.15, holdout: 0.15, shuffle: true, seed },
          ];
          const a = await buildDataset(inputsWith(traces, rules, seed));
          const b = await buildDataset(inputsWith(traces, rules, seed));

          if (a.datasetId !== b.datasetId) return false;

          for (const f of ["train.jsonl", "eval.jsonl", "holdout.jsonl", "manifest.json", "cluster-stats.json"]) {
            const aBytes = readFileSync(join(a.writePath, f), "utf-8");
            const bBytes = readFileSync(join(b.writePath, f), "utf-8");
            if (aBytes !== bBytes) return false;
          }
          return true;
        },
      ),
      { numRuns: 100 },
    );
  }, 120000);

  test("different seed → different split (unless traces fit entirely in one partition)", async () => {
    const traces = traceBatch(8, ["x"]);
    const rulesA: SelectionRule[] = [
      { type: "split", train: 0.5, eval: 0.25, holdout: 0.25, shuffle: true, seed: 1 },
    ];
    const rulesB: SelectionRule[] = [
      { type: "split", train: 0.5, eval: 0.25, holdout: 0.25, shuffle: true, seed: 2 },
    ];
    const a = await buildDataset(inputsWith(traces, rulesA, 1));
    const b = await buildDataset(inputsWith(traces, rulesB, 2));
    const aTrain = readFileSync(join(a.writePath, "train.jsonl"), "utf-8");
    const bTrain = readFileSync(join(b.writePath, "train.jsonl"), "utf-8");
    // datasetId should differ (different rule seeds → different configHash).
    expect(a.datasetId).not.toBe(b.datasetId);
    // Contents likely differ too.
    expect(aTrain).not.toBe(bTrain);
  });
});
