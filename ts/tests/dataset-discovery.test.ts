/**
 * AC-461: Repo-local dataset discovery and schema adaptation.
 *
 * Tests the discovery engine that finds candidate training data in a
 * repo tree, and the adapter pipeline that converts repo-local formats
 * into the distillation training schema with provenance.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync, existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  DatasetDiscovery,
  DatasetAdapter,
  type DiscoveredDataset,
  type AdaptedDataset,
  type DiscoveryManifest,
} from "../src/traces/dataset-discovery.js";

let tmpDir: string;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "ac-461-test-"));
});
afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

// Helper: create a repo tree with various data files
function seedRepoTree() {
  // JSONL training data (already in export format)
  mkdirSync(join(tmpDir, "data"), { recursive: true });
  writeFileSync(join(tmpDir, "data", "train.jsonl"),
    '{"conversations":[{"from":"human","value":"Hello"},{"from":"gpt","value":"Hi"}]}\n' +
    '{"conversations":[{"from":"human","value":"Fix bug"},{"from":"gpt","value":"Fixed"}]}\n',
    "utf-8");

  // JSON fixtures
  mkdirSync(join(tmpDir, "fixtures"), { recursive: true });
  writeFileSync(join(tmpDir, "fixtures", "examples.json"),
    JSON.stringify([
      { input: "What is 2+2?", output: "4", score: 1.0 },
      { input: "Write a poem", output: "Roses are red...", score: 0.8 },
    ]),
    "utf-8");

  // CSV data
  mkdirSync(join(tmpDir, "benchmarks"), { recursive: true });
  writeFileSync(join(tmpDir, "benchmarks", "eval.csv"),
    "prompt,response,score\n\"Explain ML\",\"Machine learning is...\",0.9\n\"Write code\",\"def hello():\",0.7\n",
    "utf-8");

  // Markdown examples
  mkdirSync(join(tmpDir, "examples"), { recursive: true });
  writeFileSync(join(tmpDir, "examples", "task.md"),
    "# Task: Summarize\n\n## Input\nLong document...\n\n## Expected Output\nShort summary.\n",
    "utf-8");

  // Manifest declaring sources
  writeFileSync(join(tmpDir, ".autoctx-data.json"),
    JSON.stringify({
      datasets: [
        { path: "data/train.jsonl", format: "sharegpt_jsonl", scenario: "general" },
        { path: "fixtures/examples.json", format: "io_pairs_json", scenario: "qa" },
        { path: "benchmarks/eval.csv", format: "csv", scenario: "eval" },
      ],
    }),
    "utf-8");

  // Non-data files that should NOT be discovered
  writeFileSync(join(tmpDir, "README.md"), "# My Project\n", "utf-8");
  writeFileSync(join(tmpDir, "package.json"), '{"name":"test"}', "utf-8");
}

// ---------------------------------------------------------------------------
// Dataset discovery
// ---------------------------------------------------------------------------

describe("DatasetDiscovery", () => {
  it("discovers datasets from conventional directories", () => {
    seedRepoTree();
    const discovery = new DatasetDiscovery();
    const results = discovery.scan(tmpDir);

    expect(results.length).toBeGreaterThan(0);
    const paths = results.map((r) => r.relativePath);
    expect(paths.some((p) => p.includes("data/train.jsonl"))).toBe(true);
  });

  it("discovers from manifest file when present", () => {
    seedRepoTree();
    const discovery = new DatasetDiscovery();
    const results = discovery.scan(tmpDir);

    const manifestEntries = results.filter((r) => r.source === "manifest");
    expect(manifestEntries.length).toBe(3); // 3 entries in .autoctx-data.json
  });

  it("discovers CSV, JSON, JSONL, and Markdown files", () => {
    seedRepoTree();
    const discovery = new DatasetDiscovery();
    const results = discovery.scan(tmpDir);

    const formats = new Set(results.map((r) => r.format));
    expect(formats.has("jsonl")).toBe(true);
    expect(formats.has("json")).toBe(true);
    expect(formats.has("csv")).toBe(true);
  });

  it("does not discover non-data files", () => {
    seedRepoTree();
    const discovery = new DatasetDiscovery();
    const results = discovery.scan(tmpDir);

    const paths = results.map((r) => r.relativePath);
    expect(paths.some((p) => p === "README.md")).toBe(false);
    expect(paths.some((p) => p === "package.json")).toBe(false);
  });

  it("returns empty for repo with no data files", () => {
    mkdirSync(join(tmpDir, "src"), { recursive: true });
    writeFileSync(join(tmpDir, "src", "index.ts"), "export {};", "utf-8");

    const discovery = new DatasetDiscovery();
    const results = discovery.scan(tmpDir);
    expect(results.length).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// Dataset adaptation
// ---------------------------------------------------------------------------

describe("DatasetAdapter", () => {
  it("adapts JSONL ShareGPT data (passthrough)", () => {
    seedRepoTree();
    const adapter = new DatasetAdapter();
    const result = adapter.adapt({
      absolutePath: join(tmpDir, "data", "train.jsonl"),
      relativePath: "data/train.jsonl",
      format: "jsonl",
      source: "conventional_dir",
    });

    expect(result.status).toBe("adapted");
    expect(result.records.length).toBe(2);
    expect(result.records[0].conversations).toBeDefined();
    expect(result.provenance.sourceFormat).toBe("jsonl");
  });

  it("adapts JSON input/output pairs to ShareGPT", () => {
    seedRepoTree();
    const adapter = new DatasetAdapter();
    const result = adapter.adapt({
      absolutePath: join(tmpDir, "fixtures", "examples.json"),
      relativePath: "fixtures/examples.json",
      format: "json",
      source: "conventional_dir",
    });

    expect(result.status).toBe("adapted");
    expect(result.records.length).toBe(2);
    expect(result.records[0].conversations[0].from).toBe("human");
    expect(result.records[0].conversations[1].from).toBe("gpt");
  });

  it("adapts CSV to ShareGPT", () => {
    seedRepoTree();
    const adapter = new DatasetAdapter();
    const result = adapter.adapt({
      absolutePath: join(tmpDir, "benchmarks", "eval.csv"),
      relativePath: "benchmarks/eval.csv",
      format: "csv",
      source: "conventional_dir",
    });

    expect(result.status).toBe("adapted");
    expect(result.records.length).toBe(2);
    expect(result.records[0].conversations).toBeDefined();
  });

  it("preserves provenance in adapted records", () => {
    seedRepoTree();
    const adapter = new DatasetAdapter();
    const result = adapter.adapt({
      absolutePath: join(tmpDir, "fixtures", "examples.json"),
      relativePath: "fixtures/examples.json",
      format: "json",
      source: "manifest",
      scenario: "qa",
    });

    expect(result.provenance.sourcePath).toBe("fixtures/examples.json");
    expect(result.provenance.sourceFormat).toBe("json");
    expect(result.provenance.scenario).toBe("qa");
    expect(result.provenance.adaptedAt).toBeTruthy();
  });

  it("fails gracefully for unreadable files", () => {
    const adapter = new DatasetAdapter();
    const result = adapter.adapt({
      absolutePath: join(tmpDir, "nonexistent.json"),
      relativePath: "nonexistent.json",
      format: "json",
      source: "conventional_dir",
    });

    expect(result.status).toBe("failed");
    expect(result.error).toContain("not found");
  });
});

// ---------------------------------------------------------------------------
// Full pipeline: discover → adapt → output
// ---------------------------------------------------------------------------

describe("discover + adapt pipeline", () => {
  it("discovers and adapts all datasets in a repo tree", () => {
    seedRepoTree();
    const discovery = new DatasetDiscovery();
    const adapter = new DatasetAdapter();

    const discovered = discovery.scan(tmpDir);
    const adapted: AdaptedDataset[] = [];
    for (const d of discovered) {
      const result = adapter.adapt(d);
      if (result.status === "adapted") adapted.push(result);
    }

    expect(adapted.length).toBeGreaterThanOrEqual(3); // JSONL, JSON, CSV
    const totalRecords = adapted.reduce((sum, a) => sum + a.records.length, 0);
    expect(totalRecords).toBeGreaterThanOrEqual(6);
  });
});
