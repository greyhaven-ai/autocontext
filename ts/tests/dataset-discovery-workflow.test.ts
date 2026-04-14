import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join, relative } from "node:path";
import { tmpdir } from "node:os";

import {
  collectManifestDatasets,
  detectDatasetFormat,
  discoverDatasets,
  resolveRepoLocalDatasetPath,
} from "../src/traces/dataset-discovery-workflow.js";

let tmpDir: string;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "ac-dataset-discovery-workflow-"));
});

afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

describe("dataset discovery workflow", () => {
  it("resolves repo-local paths and detects dataset formats from hints and extensions", () => {
    expect(resolveRepoLocalDatasetPath(tmpDir, "data/train.jsonl")).toBe(join(tmpDir, "data/train.jsonl"));
    expect(resolveRepoLocalDatasetPath(tmpDir, "../escape.jsonl")).toBeNull();
    expect(detectDatasetFormat("examples/task.md")).toBe("markdown");
    expect(detectDatasetFormat("custom.txt", "sharegpt_jsonl")).toBe("jsonl");
    expect(detectDatasetFormat("custom.txt", "io_pairs_json")).toBe("json");
  });

  it("collects manifest datasets and avoids duplicating manifest paths during conventional scanning", () => {
    mkdirSync(join(tmpDir, "data"), { recursive: true });
    mkdirSync(join(tmpDir, "fixtures"), { recursive: true });
    writeFileSync(join(tmpDir, "data", "train.jsonl"), '{"conversations":[]}\n', "utf-8");
    writeFileSync(join(tmpDir, "fixtures", "eval.json"), JSON.stringify([{ input: "hi", output: "hello" }]), "utf-8");
    writeFileSync(
      join(tmpDir, ".autoctx-data.json"),
      JSON.stringify({
        datasets: [
          { path: "data/train.jsonl", format: "sharegpt_jsonl", scenario: "general" },
          { path: "fixtures/eval.json", format: "io_pairs_json", scenario: "qa" },
        ],
      }),
      "utf-8",
    );

    const manifestDatasets = collectManifestDatasets(tmpDir);
    expect(manifestDatasets).toHaveLength(2);
    expect(manifestDatasets[0]).toMatchObject({
      relativePath: "data/train.jsonl",
      source: "manifest",
      format: "jsonl",
    });

    const discovered = discoverDatasets(tmpDir);
    const discoveredPaths = discovered.map((dataset) => dataset.relativePath);
    expect(discoveredPaths.filter((path) => path === "data/train.jsonl")).toHaveLength(1);
    expect(discoveredPaths.filter((path) => path === "fixtures/eval.json")).toHaveLength(1);
  });

  it("ignores manifest datasets that resolve outside the repo root", () => {
    const outsideDir = mkdtempSync(join(tmpdir(), "ac-dataset-discovery-outside-"));
    try {
      const outsidePath = join(outsideDir, "secret.json");
      writeFileSync(outsidePath, JSON.stringify([{ input: "leak", output: "nope" }]), "utf-8");
      writeFileSync(
        join(tmpDir, ".autoctx-data.json"),
        JSON.stringify({
          datasets: [{ path: relative(tmpDir, outsidePath), format: "json" }],
        }),
        "utf-8",
      );

      expect(collectManifestDatasets(tmpDir)).toEqual([]);
    } finally {
      rmSync(outsideDir, { recursive: true, force: true });
    }
  });
});
