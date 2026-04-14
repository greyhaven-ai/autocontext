import { describe, expect, it, vi } from "vitest";

import {
  buildFailedInvestigationRunResult,
  resolveInvestigationRunDependencies,
} from "../src/investigation/investigation-run-support-workflow.js";

describe("investigation run support workflow", () => {
  it("resolves run dependencies with override precedence", () => {
    const override = vi.fn();
    const resolved = resolveInvestigationRunDependencies({
      buildInvestigationSpec: override as any,
    });

    expect(resolved.buildInvestigationSpec).toBe(override);
    expect(typeof resolved.generateScenarioSource).toBe("function");
    expect(typeof resolved.executeGeneratedInvestigation).toBe("function");
  });

  it("builds failed investigation results through the injected failure builder", () => {
    const buildFailedInvestigationResult = vi.fn(() => ({
      id: "inv-4",
      name: "checkout_rca",
      family: "investigation" as const,
      status: "failed" as const,
      description: "Investigate checkout regression",
      question: "Investigate checkout regression",
      hypotheses: [],
      evidence: [],
      conclusion: { bestExplanation: "", confidence: 0, limitations: ["provider offline"] },
      unknowns: [],
      recommendedNextSteps: [],
      stepsExecuted: 0,
      artifacts: { investigationDir: "" },
      error: "provider offline",
    }));

    const result = buildFailedInvestigationRunResult({
      id: "inv-4",
      name: "checkout_rca",
      request: { description: "Investigate checkout regression" },
      errors: ["provider offline"],
      dependencies: { buildFailedInvestigationResult },
    });

    expect(result.status).toBe("failed");
    expect(buildFailedInvestigationResult).toHaveBeenCalledWith(
      "inv-4",
      "checkout_rca",
      { description: "Investigate checkout regression" },
      ["provider offline"],
    );
  });
});
