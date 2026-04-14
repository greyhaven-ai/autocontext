import { describe, expect, it } from "vitest";
import { join } from "node:path";
import { tmpdir } from "node:os";

import {
  detectDatasetFormat,
  resolveRepoLocalDatasetPath,
} from "../src/traces/dataset-path-resolution-workflow.js";

describe("dataset path resolution workflow", () => {
  it("keeps dataset paths repo-local and detects formats from hints and extensions", () => {
    const repoRoot = join(tmpdir(), "ac-dataset-path-resolution");

    expect(resolveRepoLocalDatasetPath(repoRoot, "data/train.jsonl")).toBe(join(repoRoot, "data/train.jsonl"));
    expect(resolveRepoLocalDatasetPath(repoRoot, "../escape.jsonl")).toBeNull();

    expect(detectDatasetFormat("examples/task.md")).toBe("markdown");
    expect(detectDatasetFormat("custom.txt", "sharegpt_jsonl")).toBe("jsonl");
    expect(detectDatasetFormat("custom.txt", "io_pairs_json")).toBe("json");
    expect(detectDatasetFormat("custom.txt", "csv_export")).toBe("csv");
    expect(detectDatasetFormat("custom.txt")).toBe("unknown");
  });
});
