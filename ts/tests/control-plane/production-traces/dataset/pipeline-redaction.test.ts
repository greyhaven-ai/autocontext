/**
 * Pipeline export-boundary redaction tests (spec §7.5).
 *
 * The redaction policy is applied once per trace at the row-assembly boundary.
 * Every emitted DatasetRow carries `source.redactionApplied: true`.
 */
import { describe, test, expect } from "vitest";
import { mkdtempSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { buildDataset } from "../../../../src/production-traces/dataset/pipeline.js";
import { MINIMAL_POLICY, makeTrace } from "./_helpers/fixtures.js";
import type {
  BuildDatasetInputs,
  Rubric,
} from "../../../../src/production-traces/dataset/types.js";
import type { LoadedRedactionPolicy } from "../../../../src/production-traces/redaction/types.js";
import type { ProductionTrace } from "../../../../src/production-traces/contract/types.js";

const RUBRIC: Rubric = { rubricId: "r", dimensions: ["a"] };

function baseInputs(
  traces: readonly ProductionTrace[],
  policy: LoadedRedactionPolicy,
): BuildDatasetInputs {
  return {
    cwd: mkdtempSync(join(tmpdir(), "red-")),
    name: "redact-test",
    description: "",
    traces,
    clusterStrategy: "taskType",
    selectionRules: [],
    rubricConfig: {
      rubricsByCluster: {
        x: { source: "inline", rubric: RUBRIC },
        uncategorized: { source: "inline", rubric: RUBRIC },
      },
    },
    allowSyntheticRubrics: false,
    redactionPolicy: policy,
    installSalt: null,
    seed: 0,
    autoctxVersion: "0.4.3-test",
  };
}

describe("export-boundary redaction", () => {
  test("PII markers rewrite message content to placeholder", async () => {
    const trace: ProductionTrace = makeTrace({
      traceId: "01K00000000000000000000001",
      taskType: "x",
      messages: [
        { role: "user", content: "email alice@example.com", timestamp: "2026-04-17T12:00:00.000Z" },
      ],
    });
    // Hand-inject a marker (tests bypass the mark phase).
    const withMarker: ProductionTrace = {
      ...trace,
      redactions: [
        {
          path: "/messages/0/content",
          reason: "pii-email",
          category: "pii-email",
          detectedBy: "ingestion",
          detectedAt: "2026-04-17T12:00:00.500Z",
        },
      ],
    };
    const res = await buildDataset(baseInputs([withMarker], MINIMAL_POLICY));
    const lines = readFileSync(join(res.writePath, "train.jsonl"), "utf-8").trim().split("\n");
    expect(lines.length).toBe(1);
    const row = JSON.parse(lines[0]);
    expect(row.inputs.messages[0].content).toBe("[redacted]");
    expect(row.source.redactionApplied).toBe(true);
  });

  test("rawProviderPayload in metadata is stripped by default (includeRawProviderPayload=false)", async () => {
    const trace: ProductionTrace = makeTrace({
      traceId: "01K00000000000000000000001",
      taskType: "x",
    });
    const withRaw: ProductionTrace = {
      ...trace,
      metadata: {
        rawProviderPayload: { anything: "secret" },
        safe: "keep-me",
      },
    };
    const res = await buildDataset(baseInputs([withRaw], MINIMAL_POLICY));
    // Note: DatasetRow.metadata is { } always — the pipeline doesn't surface
    // trace-level metadata onto the row by default. We instead assert the
    // redaction phase ran by checking redactionApplied.
    const row = JSON.parse(readFileSync(join(res.writePath, "train.jsonl"), "utf-8").trim());
    expect(row.source.redactionApplied).toBe(true);
    // No raw payload leaks into the row (row.metadata is always an empty object
    // per current design; this test is mainly a check that the pipeline runs
    // against redacted traces rather than raw ones).
    expect(JSON.stringify(row)).not.toContain("secret");
    expect(JSON.stringify(row)).not.toContain("rawProviderPayload");
  });

  test("redactionApplied flag is true on every row in every split", async () => {
    const traces = Array.from({ length: 5 }, (_, i) =>
      makeTrace({
        traceId: `01K0000000000000000000000${i}`,
        taskType: "x",
      }),
    );
    const res = await buildDataset({
      ...baseInputs(traces, MINIMAL_POLICY),
      selectionRules: [
        { type: "split", train: 0.6, eval: 0.2, holdout: 0.2 },
      ],
    });
    for (const f of ["train.jsonl", "eval.jsonl", "holdout.jsonl"]) {
      const content = readFileSync(join(res.writePath, f), "utf-8");
      const lines = content.trim().split("\n").filter((l) => l.length > 0);
      for (const line of lines) {
        const row = JSON.parse(line);
        expect(row.source.redactionApplied).toBe(true);
      }
    }
  });
});
