import { describe, expect, it, vi } from "vitest";

import { executeInvestigationAnalysisResult } from "../src/investigation/investigation-analysis-result-workflow.js";
import type { LLMProvider } from "../src/types/index.js";

describe("investigation analysis/result workflow", () => {
  it("executes analysis and persists the completed investigation report", async () => {
    const persistInvestigationReport = vi.fn();
    const buildCompletedInvestigationResult = vi.fn(() => ({
      id: "inv-2",
      name: "incident_rca",
      family: "investigation",
      status: "completed" as const,
      description: "Investigate incident",
      question: "What caused the incident?",
      hypotheses: [{ id: "h0", statement: "Config drift", status: "supported" as const, confidence: 0.8 }],
      evidence: [{ id: "e0", kind: "observation", source: "scenario execution", summary: "Config drift observed", supports: ["h0"], contradicts: [], isRedHerring: false }],
      conclusion: { bestExplanation: "Config drift", confidence: 0.8, limitations: [] },
      unknowns: [],
      recommendedNextSteps: ["Verify leading hypothesis: \"Config drift\""],
      stepsExecuted: 2,
      artifacts: { investigationDir: "/tmp/knowledge/_investigations/incident_rca", reportPath: "/tmp/knowledge/_investigations/incident_rca/report.json" },
    }));

    const result = await executeInvestigationAnalysisResult(
      {
        id: "inv-2",
        name: "incident_rca",
        request: { description: "Investigate incident", maxSteps: 2, maxHypotheses: 2 },
        provider: {} as LLMProvider,
        source: "module.exports = { scenario: {} }",
        healedSpec: { diagnosis_target: "config drift" },
        investigationDir: "/tmp/knowledge/_investigations/incident_rca",
      },
      {
        executeGeneratedInvestigation: vi.fn(async () => ({
          stepsExecuted: 2,
          collectedEvidence: [{ id: "e0", content: "Config drift observed", isRedHerring: false, relevance: 0.8 }],
          finalState: {},
        })),
        generateInvestigationHypotheses: vi.fn(async () => ({
          question: "What caused the incident?",
          hypotheses: [{ statement: "Config drift", confidence: 0.8 }],
        })),
        buildInvestigationEvidence: vi.fn(() => [{
          id: "e0",
          kind: "observation",
          source: "scenario execution",
          summary: "Config drift observed",
          supports: [],
          contradicts: [],
          isRedHerring: false,
        }]),
        evaluateInvestigationHypotheses: vi.fn(() => ({
          evidence: [{
            id: "e0",
            kind: "observation",
            source: "scenario execution",
            summary: "Config drift observed",
            supports: ["h0"],
            contradicts: [],
            isRedHerring: false,
          }],
          hypotheses: [{ id: "h0", statement: "Config drift", status: "supported" as const, confidence: 0.8 }],
        })) as any,
        buildInvestigationConclusion: vi.fn(() => ({
          bestExplanation: "Config drift",
          confidence: 0.8,
          limitations: [],
        })),
        identifyInvestigationUnknowns: vi.fn(() => []),
        recommendInvestigationNextSteps: vi.fn(() => ["Verify leading hypothesis: \"Config drift\""]),
        buildCompletedInvestigationResult: buildCompletedInvestigationResult as any,
        persistInvestigationReport,
      },
    );

    expect(result.status).toBe("completed");
    expect(buildCompletedInvestigationResult).toHaveBeenCalledWith(
      expect.objectContaining({
        id: "inv-2",
        name: "incident_rca",
        reportPath: "/tmp/knowledge/_investigations/incident_rca/report.json",
        stepsExecuted: 2,
      }),
    );
    expect(persistInvestigationReport).toHaveBeenCalledWith(
      "/tmp/knowledge/_investigations/incident_rca/report.json",
      result,
    );
  });
});
