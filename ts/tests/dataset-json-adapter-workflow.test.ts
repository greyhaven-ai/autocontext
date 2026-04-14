import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  adaptJsonDataset,
  adaptJsonlDataset,
  ioPairToShareGPT,
} from "../src/traces/dataset-json-adapter-workflow.js";

describe("dataset json adapter workflow", () => {
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = mkdtempSync(join(tmpdir(), "ac-dataset-json-adapter-"));
  });

  afterEach(() => {
    rmSync(tmpDir, { recursive: true, force: true });
  });

  it("adapts jsonl sharegpt and io-pair rows while collecting parse warnings", () => {
    const datasetPath = join(tmpDir, "train.jsonl");
    writeFileSync(
      datasetPath,
      [
        '{"conversations":[{"from":"human","value":"hi"},{"from":"gpt","value":"hello"}]}',
        '{"input":"Prompt","output":"Response","score":0.8}',
        'not json',
      ].join("\n"),
      "utf-8",
    );

    const warnings: string[] = [];
    const records = adaptJsonlDataset(datasetPath, warnings);
    expect(records).toHaveLength(2);
    expect(records[1]).toEqual(ioPairToShareGPT({ input: "Prompt", output: "Response", score: 0.8 }));
    expect(warnings[0]).toContain("Line 3");
  });

  it("adapts array and single-object json datasets", () => {
    const arrayPath = join(tmpDir, "examples.json");
    writeFileSync(
      arrayPath,
      JSON.stringify([
        { input: "Question", output: "Answer" },
        { conversations: [{ from: "human", value: "h" }, { from: "gpt", value: "g" }] },
      ]),
      "utf-8",
    );
    expect(adaptJsonDataset(arrayPath)).toHaveLength(2);

    const singlePath = join(tmpDir, "single.json");
    writeFileSync(singlePath, JSON.stringify({ prompt: "Do X", response: "Done" }), "utf-8");
    expect(adaptJsonDataset(singlePath)).toEqual([
      {
        conversations: [
          { from: "human", value: "Do X" },
          { from: "gpt", value: "Done" },
        ],
        metadata: undefined,
      },
    ]);
  });
});
