import { describe, expect, it } from "vitest";

import { SolveGenerationBudget } from "../src/knowledge/solve-generation-budget.js";

describe("solve generation budget", () => {
  it("allows unlimited budgets and raises once elapsed time exceeds the cap", () => {
    let nowMs = 0;
    const unlimited = new SolveGenerationBudget({
      scenarioName: "grid_ctf",
      budgetSeconds: 0,
      nowMs: () => 10_000,
    });
    expect(() => unlimited.check("setup")).not.toThrow();

    const budget = new SolveGenerationBudget({
      scenarioName: "incident_triage",
      budgetSeconds: 2,
      nowMs: () => nowMs,
    });
    expect(() => budget.check("initial generation")).not.toThrow();

    nowMs = 2_001;
    expect(() => budget.check("evaluation")).toThrow(
      "Solve generation time budget exceeded during evaluation after 2.00s for scenario 'incident_triage' (budget 2s)",
    );
  });
});
