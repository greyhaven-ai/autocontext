import { describe, test, expect } from "vitest";
import { buildManifest } from "../../../../src/production-traces/dataset/manifest.js";
import {
  validateDatasetManifest,
  validateDatasetRow,
  validateSelectionRule,
  validateClusterConfig,
  validateRubricConfig,
} from "../../../../src/production-traces/contract/validators.js";
import { deriveDatasetId } from "../../../../src/production-traces/contract/content-address.js";
import {
  computeConfigHash,
  computeInputTracesHash,
  computeFileHash,
} from "../../../../src/production-traces/dataset/provenance.js";
import { parseDatasetId } from "../../../../src/production-traces/dataset/types.js";
import { traceIdOf } from "./_helpers/fixtures.js";

describe("buildManifest", () => {
  const configHash = computeConfigHash({ k: "v" });
  const inputTracesHash = computeInputTracesHash([traceIdOf("01K00000000000000000000001")]);
  const datasetId = parseDatasetId(`ds_${deriveDatasetId(configHash, inputTracesHash).slice(3)}`);

  test("manifest validates against dataset-manifest.schema.json", () => {
    if (datasetId === null) throw new Error("setup: datasetId invalid");
    const fileHash = computeFileHash("row1\nrow2\n");
    const manifest = buildManifest({
      datasetId,
      name: "demo",
      description: "",
      createdAt: "2026-04-17T12:00:00.000Z",
      autoctxVersion: "0.4.3",
      traceCount: 2,
      timeRange: { from: "2026-04-17T12:00:00.000Z", to: "2026-04-17T12:00:01.000Z" },
      clusterStrategy: "taskType",
      filterRules: [{ type: "gate" }],
      redactionPolicy: { mode: "on-export", snapshotHash: configHash },
      splits: {
        train:   { rowCount: 2, fileHash },
        eval:    { rowCount: 0, fileHash },
        holdout: { rowCount: 0, fileHash },
      },
      clusters: [{ clusterId: "x", size: 2 }],
      provenance: { configHash, inputTracesHash },
    });
    const result = validateDatasetManifest(manifest);
    if (!result.valid) {
      throw new Error(`validation errors: ${result.errors.join("; ")}`);
    }
    expect(result.valid).toBe(true);
  });

  test("datasetId derivation is deterministic", () => {
    const a = deriveDatasetId(configHash, inputTracesHash);
    const b = deriveDatasetId(configHash, inputTracesHash);
    expect(a).toBe(b);
    expect(a).toMatch(/^ds_[0-9A-HJKMNP-TV-Z]{26}$/);
  });
});

describe("schema validation smoke tests", () => {
  test("selection-rule schema accepts each rule variant", () => {
    expect(validateSelectionRule({ type: "gate" }).valid).toBe(true);
    expect(validateSelectionRule({ type: "top-quartile", by: "outcome.score", percentile: 75 }).valid).toBe(true);
    expect(validateSelectionRule({
      type: "contrastive",
      failureCriterion: { "outcome.label": { equals: "failure" } },
      successCriterion: { "outcome.label": { equals: "success" } },
    }).valid).toBe(true);
    expect(validateSelectionRule({ type: "split", train: 0.7, eval: 0.15, holdout: 0.15 }).valid).toBe(true);
  });

  test("selection-rule schema rejects unknown variant", () => {
    expect(validateSelectionRule({ type: "bogus" }).valid).toBe(false);
  });

  test("cluster-config schema requires strategy: rules", () => {
    expect(validateClusterConfig({
      strategy: "rules",
      rules: [{ id: "x", match: { default: { default: true } } }],
    }).valid).toBe(true);
    expect(validateClusterConfig({ strategy: "other", rules: [] }).valid).toBe(false);
  });

  test("rubric-config schema accepts inline and file entries", () => {
    expect(validateRubricConfig({
      rubricsByCluster: {
        x: { source: "inline", rubric: { rubricId: "r", dimensions: ["a"] } },
        y: { source: "file", path: "/tmp/r.json" },
      },
    }).valid).toBe(true);
  });

  test("dataset-row schema smoke", () => {
    const row = {
      schemaVersion: "1.0",
      rowId: "01K00000000000000000000001",
      split: "train",
      clusterId: "x",
      source: {
        traceIds: ["01K00000000000000000000002"],
        timeRange: { from: "2026-04-17T12:00:00.000Z", to: "2026-04-17T12:00:01.000Z" },
        redactionApplied: true,
      },
      inputs: {
        messages: [{ role: "user", content: "hi", timestamp: "2026-04-17T12:00:00.000Z" }],
        toolsAvailable: [],
      },
      metadata: {},
    };
    const result = validateDatasetRow(row);
    if (!result.valid) throw new Error(result.errors.join("; "));
    expect(result.valid).toBe(true);
  });
});
