import { describe, expect, it } from "vitest";

import {
  buildAgentTaskSolvePackage,
  buildGeneratedScenarioSolvePackage,
  createSolveJob,
  failSolveJob,
  getCompletedSolveJobResult,
  getSolveJobStatus,
} from "../src/knowledge/solve-workflow.js";

describe("solve workflow", () => {
  it("creates solve jobs and reports status payloads", () => {
    const job = createSolveJob("solve_123", "Summarize outage escalations", 3, {
      familyOverride: "agent_task",
      generationTimeBudgetSeconds: 15,
    });

    expect(job).toMatchObject({
      jobId: "solve_123",
      description: "Summarize outage escalations",
      generations: 3,
      familyOverride: "agent_task",
      generationTimeBudgetSeconds: 15,
      llmClassifierFallbackUsed: false,
      status: "pending",
    });

    expect(getSolveJobStatus("solve_123", job)).toMatchObject({
      jobId: "solve_123",
      status: "pending",
      generations: 3,
      familyOverride: "agent_task",
      generationTimeBudgetSeconds: 15,
      generation_time_budget_seconds: 15,
      llmClassifierFallbackUsed: false,
      llm_classifier_fallback_used: false,
      progress: 0,
    });

    expect(getSolveJobStatus("missing", undefined)).toMatchObject({
      jobId: "missing",
      status: "not_found",
    });
  });

  it("fails jobs and hides incomplete results", () => {
    const job = createSolveJob("solve_123", "Summarize outage escalations", 3);

    failSolveJob(job, new Error("boom"));

    expect(job.status).toBe("failed");
    expect(job.error).toBe("boom");
    expect(getCompletedSolveJobResult(job)).toBeNull();
  });

  it("builds serialized agent-task solve packages", () => {
    const pkg = buildAgentTaskSolvePackage({
      scenarioName: "incident_triage",
      description: "Incident triage",
      taskPrompt: "Summarize the incident and assign an owner.",
      judgeRubric: "Evaluate completeness.",
      outputFormat: "free_text",
      maxRounds: 2,
      qualityThreshold: 0.9,
      bestRound: 2,
      totalRounds: 2,
      terminationReason: "threshold_met",
      bestScore: 0.92,
      bestOutput: "Owner: on-call",
      judgeFailures: 0,
      bestReasoning: "Added explicit owner assignment.",
    });

    expect(pkg.scenario_name).toBe("incident_triage");
    expect(pkg.best_score).toBe(0.92);
    expect(pkg.skill_markdown).toContain("Best round: 2");
    expect(pkg.example_outputs?.[0]?.output).toContain("Owner: on-call");
  });

  it("builds serialized generated-scenario solve packages", () => {
    const pkg = buildGeneratedScenarioSolvePackage({
      scenarioName: "outage_investigation",
      family: "investigation",
      description: "Outage investigation",
      score: 0.84,
      reasoning: "Gathered evidence before diagnosis.",
      dimensionScores: { evidence: 0.9, diagnosis: 0.7 },
      records: [
        { action: { name: "collect_logs" } },
        { action: { name: "form_hypothesis" } },
      ],
      stepsExecuted: 2,
      validation: { durationMs: 15, executedMethods: ["initialState", "getResult"] },
    });

    expect(pkg.scenario_name).toBe("outage_investigation");
    expect(pkg.metadata?.family).toBe("investigation");
    expect(pkg.skill_markdown).toContain("collect_logs");
    expect(pkg.lessons).toContain("Gathered evidence before diagnosis.");
  });
});
