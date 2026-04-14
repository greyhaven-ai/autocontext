import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

import {
  adaptDiscoveredDataset,
  buildDatasetProvenance,
  findMarkdownSection,
  ioPairToShareGPT,
  normalizeMarkdownHeading,
  parseCSVLine,
  parseMarkdownSections,
} from "../src/traces/dataset-adapter-workflow.js";
import type { DiscoveredDataset } from "../src/traces/dataset-discovery-types.js";

let tmpDir: string;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "ac-dataset-adapter-workflow-"));
});

afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

describe("dataset adapter workflow", () => {
  it("builds provenance and adapts JSONL with warnings", () => {
    mkdirSync(join(tmpDir, "data"), { recursive: true });
    const datasetPath = join(tmpDir, "data", "train.jsonl");
    writeFileSync(
      datasetPath,
      '{"conversations":[{"from":"human","value":"hi"},{"from":"gpt","value":"hello"}]}\nnot json\n',
      "utf-8",
    );
    const dataset: DiscoveredDataset = {
      absolutePath: datasetPath,
      relativePath: "data/train.jsonl",
      format: "jsonl",
      source: "conventional_dir",
      scenario: "qa",
    };

    const provenance = buildDatasetProvenance(dataset);
    expect(provenance).toMatchObject({
      sourcePath: "data/train.jsonl",
      sourceFormat: "jsonl",
      scenario: "qa",
      transformationMethod: "adapt_jsonl",
    });

    const result = adaptDiscoveredDataset(dataset);
    expect(result.status).toBe("adapted");
    expect(result.records).toHaveLength(1);
    expect(result.warnings[0]).toContain("Line 2");
  });

  it("parses csv quoting and normalizes markdown headings", () => {
    expect(parseCSVLine('"Hello, world",answer,"escaped ""quote"""')).toEqual([
      "Hello, world",
      "answer",
      'escaped "quote"',
    ]);
    expect(normalizeMarkdownHeading("Expected Output!")).toBe("expected output");

    const sections = parseMarkdownSections([
      "# Task",
      "Summarize the report",
      "",
      "## Expected Output",
      "Short answer",
    ].join("\n"));
    expect(findMarkdownSection(sections, ["task"])).toBe("Summarize the report");
    expect(findMarkdownSection(sections, ["output"])).toBe("Short answer");
  });

  it("converts input-output pairs and fails unsupported formats clearly", () => {
    expect(ioPairToShareGPT({ input: "Prompt", output: "Response", score: 0.8 })).toEqual({
      conversations: [
        { from: "human", value: "Prompt" },
        { from: "gpt", value: "Response" },
      ],
      metadata: { score: 0.8 },
    });

    const unsupportedPath = join(tmpDir, "notes.txt");
    writeFileSync(unsupportedPath, "hello", "utf-8");
    const result = adaptDiscoveredDataset({
      absolutePath: unsupportedPath,
      relativePath: "notes.txt",
      format: "unknown",
      source: "file_scan",
    });

    expect(result.status).toBe("failed");
    expect(result.error).toBe("Unsupported format: unknown");
  });
});
