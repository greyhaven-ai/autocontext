import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { adaptCsvDataset, parseCSVLine } from "../src/traces/dataset-csv-adapter-workflow.js";
import {
  adaptMarkdownDataset,
  findMarkdownSection,
  normalizeMarkdownHeading,
  parseMarkdownSections,
} from "../src/traces/dataset-markdown-adapter-workflow.js";

describe("dataset text adapter workflow", () => {
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = mkdtempSync(join(tmpdir(), "ac-dataset-text-adapter-"));
  });

  afterEach(() => {
    rmSync(tmpDir, { recursive: true, force: true });
  });

  it("parses csv quoting and adapts prompt/response rows", () => {
    expect(parseCSVLine('"Hello, world",answer,"escaped ""quote"""')).toEqual([
      "Hello, world",
      "answer",
      'escaped "quote"',
    ]);

    const csvPath = join(tmpDir, "eval.csv");
    writeFileSync(csvPath, [
      "prompt,response",
      '"What happened?","Deployment failed"',
      '"What next?","Roll back"',
    ].join("\n"), "utf-8");

    expect(adaptCsvDataset(csvPath)).toEqual([
      {
        conversations: [
          { from: "human", value: "What happened?" },
          { from: "gpt", value: "Deployment failed" },
        ],
      },
      {
        conversations: [
          { from: "human", value: "What next?" },
          { from: "gpt", value: "Roll back" },
        ],
      },
    ]);
  });

  it("normalizes markdown headings, finds sections, and adapts markdown tasks", () => {
    expect(normalizeMarkdownHeading("Expected Output!")).toBe("expected output");

    const markdown = [
      "# Task",
      "Summarize the incident report",
      "",
      "## Expected Output",
      "A short summary",
    ].join("\n");
    const sections = parseMarkdownSections(markdown);
    expect(findMarkdownSection(sections, ["task"])).toBe("Summarize the incident report");
    expect(findMarkdownSection(sections, ["output"])).toBe("A short summary");

    const markdownPath = join(tmpDir, "task.md");
    writeFileSync(markdownPath, markdown, "utf-8");
    expect(adaptMarkdownDataset(markdownPath)).toEqual([
      {
        conversations: [
          { from: "human", value: "Summarize the incident report" },
          { from: "gpt", value: "A short summary" },
        ],
      },
    ]);
  });
});
