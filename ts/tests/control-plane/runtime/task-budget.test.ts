import { describe, expect, test } from "vitest";
import { evaluateTaskBudget } from "../../../src/control-plane/runtime/task-budget.js";

describe("evaluateTaskBudget", () => {
  test("asks for an early artifact once the artifact checkpoint is reached", () => {
    const decision = evaluateTaskBudget({
      elapsedMs: 31_000,
      totalBudgetMs: 60_000,
      artifactWritten: false,
      checkpoints: [{ name: "artifact-first", atFraction: 0.5, requiresArtifact: true }],
    });

    expect(decision.action).toBe("write-artifact");
    expect(decision.reasons).toContain("checkpoint artifact-first requires an artifact by 50%");
  });

  test("stops when the budget is exhausted", () => {
    const decision = evaluateTaskBudget({
      elapsedMs: 61_000,
      totalBudgetMs: 60_000,
      artifactWritten: true,
      checkpoints: [],
    });

    expect(decision.action).toBe("stop");
  });
});
