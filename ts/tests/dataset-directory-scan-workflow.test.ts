import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

import {
  discoverDatasets,
  scanConventionalDatasetDirectory,
} from "../src/traces/dataset-directory-scan-workflow.js";
import { collectManifestDatasets } from "../src/traces/dataset-manifest-workflow.js";
import type { DiscoveredDataset } from "../src/traces/dataset-discovery-types.js";

describe("dataset directory scan workflow", () => {
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = mkdtempSync(join(tmpdir(), "ac-dataset-scan-"));
  });

  afterEach(() => {
    rmSync(tmpDir, { recursive: true, force: true });
  });

  it("scans nested conventional directories while ignoring manifest and package files", () => {
    mkdirSync(join(tmpDir, "data", "nested"), { recursive: true });
    writeFileSync(join(tmpDir, "data", "nested", "train.jsonl"), '{"conversations":[]}\n', "utf-8");
    writeFileSync(join(tmpDir, "data", "package.json"), '{}', "utf-8");

    const results: DiscoveredDataset[] = [];
    scanConventionalDatasetDirectory(join(tmpDir, "data"), tmpDir, results, new Set());

    expect(results).toEqual([
      expect.objectContaining({
        relativePath: join("data", "nested", "train.jsonl"),
        format: "jsonl",
        source: "conventional_dir",
      }),
    ]);
  });

  it("combines manifest and conventional discovery without duplicating manifest paths", () => {
    mkdirSync(join(tmpDir, "data"), { recursive: true });
    mkdirSync(join(tmpDir, "fixtures"), { recursive: true });
    writeFileSync(join(tmpDir, "data", "train.jsonl"), '{"conversations":[]}\n', "utf-8");
    writeFileSync(join(tmpDir, "fixtures", "eval.json"), JSON.stringify([{ input: "hi", output: "hello" }]), "utf-8");
    writeFileSync(
      join(tmpDir, ".autoctx-data.json"),
      JSON.stringify({
        datasets: [
          { path: "data/train.jsonl", format: "sharegpt_jsonl", scenario: "general" },
        ],
      }),
      "utf-8",
    );

    const manifestDatasets = collectManifestDatasets(tmpDir);
    expect(manifestDatasets).toHaveLength(1);

    const discovered = discoverDatasets(tmpDir);
    expect(discovered.filter((dataset) => dataset.relativePath === join("data", "train.jsonl"))).toHaveLength(1);
    expect(discovered.filter((dataset) => dataset.relativePath === join("fixtures", "eval.json"))).toHaveLength(1);
  });
});
