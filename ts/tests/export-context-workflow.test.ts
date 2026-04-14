import { describe, expect, it } from "vitest";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

import { ArtifactStore } from "../src/knowledge/artifact-store.js";
import {
  buildTrajectorySnippet,
  extractTrainingHints,
  resolveTrainingPromptContext,
} from "../src/training/export-context-workflow.js";

describe("training export context workflow", () => {
  it("extracts playbook hints and trajectory snippets", () => {
    const playbook = [
      "# Strategy",
      "",
      "<!-- COMPETITOR_HINTS_START -->",
      "Keep pressure on the flag carrier.",
      "<!-- COMPETITOR_HINTS_END -->",
    ].join("\n");

    expect(extractTrainingHints(playbook)).toBe("Keep pressure on the flag carrier.");
    expect(buildTrajectorySnippet([
      { generation_index: 1, best_score: 0.7, gate_decision: "advance" },
      { generation_index: 2, best_score: 0.8, gate_decision: "retry" },
    ], 1)).toEqual([
      { generation_index: 1, best_score: 0.7, gate_decision: "advance" },
    ]);
  });

  it("resolves prompt context for built-in scenarios and falls back to empty context for unknown ones", () => {
    const dir = mkdtempSync(join(tmpdir(), "ac-export-context-"));
    try {
      const artifacts = new ArtifactStore({
        runsRoot: join(dir, "runs"),
        knowledgeRoot: join(dir, "knowledge"),
      });

      expect(resolveTrainingPromptContext(artifacts, "grid_ctf")).toMatchObject({
        scenarioRules: expect.any(String),
        strategyInterface: expect.any(String),
        evaluationCriteria: expect.any(String),
      });

      expect(resolveTrainingPromptContext(artifacts, "missing_scenario")).toEqual({});
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
