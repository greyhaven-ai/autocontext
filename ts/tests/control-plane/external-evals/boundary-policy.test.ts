import { describe, expect, test } from "vitest";
import {
  assessExternalEvalBoundaryPolicy,
  buildExternalEvalDiagnosticReport,
  classifyExternalEvalTrial,
  validateExternalEvalBoundaryPolicy,
} from "../../../src/control-plane/external-evals/index.js";

describe("external eval benchmark boundary policy", () => {
  const policy = {
    mode: "discard",
    blockedPathPrefixes: ["/protected"],
    allowedPathPrefixes: ["/workspace"],
  } as const;

  test("validates explicit benchmark boundary policy configuration", () => {
    expect(validateExternalEvalBoundaryPolicy(policy)).toEqual({ valid: true });

    const invalid = validateExternalEvalBoundaryPolicy({
      mode: "discard",
      blockedPathPrefixes: [],
      allowedPathPrefixes: [],
    });

    expect(invalid.valid).toBe(false);
    if (!invalid.valid) {
      expect(invalid.errors).toContain(
        "boundary policy must declare at least one blocked or allowed path prefix",
      );
    }
  });

  test("flags normalized verifier-only path access as contaminated evidence", () => {
    const assessment = assessExternalEvalBoundaryPolicy({
      policy,
      observations: [
        {
          trialId: "task.1-of-1.tb-run-1",
          accessKind: "read",
          path: "/workspace/../protected/answer.txt",
          source: "tool-call",
        },
      ],
    });

    expect(assessment.status).toBe("discarded");
    expect(assessment.violations).toEqual([
      {
        trialId: "task.1-of-1.tb-run-1",
        accessKind: "read",
        path: "/protected/answer.txt",
        source: "tool-call",
        reason: "blocked-path-prefix",
      },
    ]);
    expect(assessment.notes).toContain(
      "boundary_violation=read /protected/answer.txt blocked-path-prefix",
    );
  });

  test("discards otherwise resolved trials when boundary policy is violated", () => {
    const boundaryAssessment = assessExternalEvalBoundaryPolicy({
      policy,
      observations: [
        {
          trialId: "task.1-of-1.tb-run-1",
          accessKind: "list",
          path: "/protected",
          source: "adapter-log",
        },
      ],
    });

    const trial = classifyExternalEvalTrial({
      taskId: "task",
      trialId: "task.1-of-1.tb-run-1",
      attempt: 1,
      isResolved: true,
      reward: 1,
      boundaryAssessment,
    });

    expect(trial).toMatchObject({
      status: "discarded",
      errorKind: "external-eval-boundary-violation",
    });
    expect(trial.reward).toBeUndefined();
    expect(trial.notes).toContain("integrity_status=discarded");
    expect(trial.notes).toContain(
      "boundary_violation=list /protected blocked-path-prefix",
    );
  });

  test("scopes run-level boundary assessments to the classified trial", () => {
    const boundaryAssessment = assessExternalEvalBoundaryPolicy({
      policy,
      observations: [
        {
          trialId: "bad",
          accessKind: "read",
          path: "/protected/answer.txt",
          source: "tool-call",
        },
      ],
    });

    const goodTrial = classifyExternalEvalTrial({
      taskId: "good-task",
      trialId: "good",
      attempt: 1,
      isResolved: true,
      boundaryAssessment,
    });
    const badTrial = classifyExternalEvalTrial({
      taskId: "bad-task",
      trialId: "bad",
      attempt: 1,
      isResolved: true,
      boundaryAssessment,
    });

    expect(goodTrial.status).toBe("passed");
    expect(goodTrial.errorKind).toBeUndefined();
    expect(goodTrial.notes).toBeUndefined();
    expect(badTrial).toMatchObject({
      status: "discarded",
      errorKind: "external-eval-boundary-violation",
    });
    expect(badTrial.notes).toContain(
      "boundary_violation=read /protected/answer.txt blocked-path-prefix",
    );
  });

  test("can report boundary contamination without changing the trial score", () => {
    const boundaryAssessment = assessExternalEvalBoundaryPolicy({
      policy: { ...policy, mode: "report-only" },
      observations: [
        {
          trialId: "task.1-of-1.tb-run-1",
          accessKind: "read",
          path: "/protected",
          source: "trace",
        },
      ],
    });

    const trial = classifyExternalEvalTrial({
      taskId: "task",
      trialId: "task.1-of-1.tb-run-1",
      attempt: 1,
      isResolved: true,
      boundaryAssessment,
    });

    expect(boundaryAssessment.status).toBe("contaminated");
    expect(trial.status).toBe("passed");
    expect(trial.reward).toBe(1);
    expect(trial.notes).toContain("integrity_status=contaminated");

    const report = buildExternalEvalDiagnosticReport({
      runId: "tb-run-1",
      createdAt: "2026-05-08T15:28:00.000Z",
      trials: [trial],
    });

    expect(report.diagnostics).toHaveLength(1);
    expect(report.diagnostics[0]).toMatchObject({
      category: "integrity-risk",
      confidence: 0.95,
    });
  });

  test("scopes run-level boundary diagnostics to the affected trial", () => {
    const boundaryAssessment = assessExternalEvalBoundaryPolicy({
      policy,
      observations: [
        {
          trialId: "bad",
          accessKind: "read",
          path: "/protected/answer.txt",
          source: "trace",
        },
      ],
    });

    const report = buildExternalEvalDiagnosticReport({
      runId: "tb-run-1",
      createdAt: "2026-05-08T15:29:00.000Z",
      trials: [
        {
          taskId: "good-task",
          trialId: "good",
          attempt: 1,
          status: "passed",
          reward: 1,
        },
        {
          taskId: "bad-task",
          trialId: "bad",
          attempt: 1,
          status: "passed",
          reward: 1,
        },
      ],
      evidence: [
        { trialId: "good", boundaryAssessment },
        { trialId: "bad", boundaryAssessment },
      ],
    });

    expect(report.diagnostics).toHaveLength(1);
    expect(report.diagnostics[0]).toMatchObject({
      trialId: "bad",
      category: "integrity-risk",
    });
  });

  test("keeps evidence-only boundary violations visible as integrity diagnostics", () => {
    const boundaryAssessment = assessExternalEvalBoundaryPolicy({
      policy,
      observations: [
        {
          trialId: "task.1-of-1.tb-run-1",
          accessKind: "search",
          path: "/",
          source: "adapter-command",
          command: "find / -name '*answer*'",
        },
      ],
    });

    const report = buildExternalEvalDiagnosticReport({
      runId: "tb-run-1",
      createdAt: "2026-05-08T15:30:00.000Z",
      trials: [
        {
          taskId: "task",
          trialId: "task.1-of-1.tb-run-1",
          attempt: 1,
          status: "passed",
          reward: 1,
        },
      ],
      evidence: [
        {
          trialId: "task.1-of-1.tb-run-1",
          boundaryAssessment,
        },
      ],
    });

    expect(report.diagnostics).toHaveLength(1);
    expect(report.diagnostics[0]).toMatchObject({
      category: "integrity-risk",
      confidence: 0.95,
    });
    expect(report.diagnostics[0]?.failureExcerpts).toEqual([
      "integrity_status=discarded",
      "boundary_violation=search / outside-allowed-path-prefix",
    ]);
    expect(report.summary.countsByCategory).toEqual({ "integrity-risk": 1 });
  });

  test("counts runtime issues even when integrity risk is the primary diagnostic", () => {
    const boundaryAssessment = assessExternalEvalBoundaryPolicy({
      policy,
      observations: [
        {
          trialId: "infra-with-boundary",
          accessKind: "read",
          path: "/protected",
          source: "adapter-log",
        },
      ],
    });

    const report = buildExternalEvalDiagnosticReport({
      runId: "tb-run-1",
      createdAt: "2026-05-08T15:31:00.000Z",
      trials: [
        {
          taskId: "infra-with-boundary",
          trialId: "infra-with-boundary",
          attempt: 1,
          status: "infrastructure-error",
          errorKind: "adapter-crash",
        },
      ],
      evidence: [
        {
          trialId: "infra-with-boundary",
          boundaryAssessment,
        },
      ],
    });

    expect(report.diagnostics).toHaveLength(1);
    expect(report.diagnostics[0]).toMatchObject({
      category: "integrity-risk",
    });
    expect(report.summary).toMatchObject({
      runtimeIssueTrials: 1,
      countsByCategory: { "integrity-risk": 1 },
    });
  });
});
