import { describe, expect, it, vi } from "vitest";

import { executeInvestigationRun } from "../src/investigation/investigation-run-workflow.js";
import type { LLMProvider } from "../src/types/index.js";

describe("investigation run workflow", () => {
  it("returns a failed result when generated investigation source does not validate", async () => {
    const buildFailedInvestigationResult = vi.fn(() => ({
      id: "inv-1",
      name: "checkout_rca",
      family: "investigation",
      status: "failed" as const,
      description: "Investigate checkout regression",
      question: "Investigate checkout regression",
      hypotheses: [],
      evidence: [],
      conclusion: { bestExplanation: "", confidence: 0, limitations: ["spec invalid"] },
      unknowns: [],
      recommendedNextSteps: [],
      stepsExecuted: 0,
      artifacts: { investigationDir: "" },
      error: "spec invalid",
    }));

    const result = await executeInvestigationRun(
      {
        id: "inv-1",
        name: "checkout_rca",
        request: { description: "Investigate checkout regression" },
        provider: {} as LLMProvider,
        knowledgeRoot: "/tmp/knowledge",
      },
      {
        buildInvestigationSpec: vi.fn(async () => ({ diagnosis_target: "config regression" })),
        healSpec: vi.fn((spec) => spec),
        generateScenarioSource: vi.fn(() => "module.exports = { scenario: {} }"),
        validateGeneratedScenario: vi.fn(async () => ({ valid: false, errors: ["spec invalid"] })),
        buildFailedInvestigationResult,
      },
    );

    expect(result.status).toBe("failed");
    expect(buildFailedInvestigationResult).toHaveBeenCalledWith(
      "inv-1",
      "checkout_rca",
      { description: "Investigate checkout regression" },
      ["spec invalid"],
    );
  });

  it("orchestrates a completed investigation and persists the report", async () => {
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

    const result = await executeInvestigationRun(
      {
        id: "inv-2",
        name: "incident_rca",
        request: { description: "Investigate incident", maxSteps: 2, maxHypotheses: 2 },
        provider: {} as LLMProvider,
        knowledgeRoot: "/tmp/knowledge",
      },
      {
        buildInvestigationSpec: vi.fn(async () => ({ diagnosis_target: "config drift" })),
        healSpec: vi.fn((spec) => spec),
        generateScenarioSource: vi.fn(() => "module.exports = { scenario: {} }"),
        validateGeneratedScenario: vi.fn(async () => ({ valid: true, errors: [] })),
        persistInvestigationArtifacts: vi.fn(() => "/tmp/knowledge/_investigations/incident_rca"),
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
          hypotheses: [{ id: "h0", statement: "Config drift", status: "supported", confidence: 0.8 }],
        })),
        buildInvestigationConclusion: vi.fn(() => ({
          bestExplanation: "Config drift",
          confidence: 0.8,
          limitations: [],
        })),
        identifyInvestigationUnknowns: vi.fn(() => []),
        recommendInvestigationNextSteps: vi.fn(() => ["Verify leading hypothesis: \"Config drift\""]),
        buildCompletedInvestigationResult,
        persistInvestigationReport,
        buildFailedInvestigationResult: vi.fn(),
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

  it("shapes thrown errors into failed investigation results", async () => {
    const buildFailedInvestigationResult = vi.fn(() => ({
      id: "inv-3",
      name: "outage_rca",
      family: "investigation",
      status: "failed" as const,
      description: "Investigate outage",
      question: "Investigate outage",
      hypotheses: [],
      evidence: [],
      conclusion: { bestExplanation: "", confidence: 0, limitations: ["provider offline"] },
      unknowns: [],
      recommendedNextSteps: [],
      stepsExecuted: 0,
      artifacts: { investigationDir: "" },
      error: "provider offline",
    }));

    const result = await executeInvestigationRun(
      {
        id: "inv-3",
        name: "outage_rca",
        request: { description: "Investigate outage" },
        provider: {} as LLMProvider,
        knowledgeRoot: "/tmp/knowledge",
      },
      {
        buildInvestigationSpec: vi.fn(async () => {
          throw new Error("provider offline");
        }),
        buildFailedInvestigationResult,
      },
    );

    expect(result.status).toBe("failed");
    expect(buildFailedInvestigationResult).toHaveBeenCalledWith(
      "inv-3",
      "outage_rca",
      { description: "Investigate outage" },
      ["provider offline"],
    );
  });
});
