import { describe, expect, it } from "vitest";

import { computeGenerationChangeVector } from "../src/analytics/credit-assignment-vector-workflow.js";

describe("credit assignment vector workflow", () => {
  it("builds a generation change vector from changed strategy surfaces", () => {
    const vector = computeGenerationChangeVector(
      3,
      0.3,
      {
        playbook: "old plan",
        tools: ["grep"],
        hints: "keep it simple",
        analysis: "weak hypothesis",
      },
      {
        playbook: "new plan with branches",
        tools: ["grep", "rg"],
        hints: "focus on invariants",
        analysis: "stronger hypothesis with evidence",
      },
    );

    expect(vector.generation).toBe(3);
    expect(vector.scoreDelta).toBe(0.3);
    expect(vector.changes.map((change) => change.component)).toEqual([
      "playbook",
      "tools",
      "hints",
      "analysis",
    ]);
  });
});
