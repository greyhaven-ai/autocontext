import { describe, expect, test } from "vitest";
import {
  buildExternalEvalDiagnosticReport,
  buildOperationalMemoryPackFromDiagnostics,
} from "../../../src/control-plane/external-evals/index.js";
import { validateOperationalMemoryPack } from "../../../src/control-plane/memory-packs/index.js";
import type { EvalTrial } from "../../../src/control-plane/contract/types.js";

const trials: EvalTrial[] = [
  {
    taskId: "git-multibranch",
    trialId: "git-multibranch.1-of-1.tb-run-1",
    attempt: 1,
    status: "failed",
    reward: 0,
  },
  {
    taskId: "nginx-request-logging",
    trialId: "nginx-request-logging.1-of-1.tb-run-1",
    attempt: 1,
    status: "failed",
    reward: 0,
  },
  {
    taskId: "polyglot-c-py",
    trialId: "polyglot-c-py.1-of-1.tb-run-1",
    attempt: 1,
    status: "infrastructure-error",
    errorKind: "agent_timeout",
    notes: ["failure_mode=agent_timeout"],
  },
];

describe("external eval diagnostics", () => {
  test("separates setup, verifier-contract, and adapter-runtime failures", () => {
    const report = buildExternalEvalDiagnosticReport({
      runId: "tb-run-1",
      createdAt: "2026-05-07T15:00:00.000Z",
      trials,
      evidence: [
        {
          trialId: "git-multibranch.1-of-1.tb-run-1",
          evidenceRefs: ["git-multibranch/sessions/tests.log"],
          verifierOutput: [
            "fatal: You are on a branch yet to be born",
            "error: src refspec main does not match any",
          ].join("\n"),
        },
        {
          trialId: "nginx-request-logging.1-of-1.tb-run-1",
          evidenceRefs: ["nginx-request-logging/sessions/tests.log"],
          verifierOutput: [
            "AssertionError: Custom log format is missing required fields",
            "Expected main: 'main branch content', got: custom content",
          ].join("\n"),
        },
        {
          trialId: "polyglot-c-py.1-of-1.tb-run-1",
          evidenceRefs: ["polyglot-c-py/results.json"],
          adapterLifecycle: {
            runId: "tb-run-1",
            taskId: "polyglot-c-py",
            trialId: "polyglot-c-py.1-of-1.tb-run-1",
            adapter: "host-codex-docker",
            command: { argv: ["codex", "exec"], cwd: "/tmp" },
            status: "timed-out",
            timeoutSource: "global-agent-timeout",
            startedAt: "2026-05-07T04:41:32.967Z",
            endedAt: "2026-05-07T14:29:41.251Z",
            artifacts: {
              stdoutPath: "agent-logs/host-codex-stdout.txt",
              stderrPath: "agent-logs/host-codex-stderr.txt",
            },
          },
        },
      ],
    });

    expect(report.diagnostics).toHaveLength(3);
    expect(report.diagnostics.map((diagnostic) => diagnostic.category)).toEqual([
      "setup-environment-failure",
      "verifier-contract-mismatch",
      "adapter-runtime-failure",
    ]);
    expect(report.summary.countsByCategory).toEqual({
      "adapter-runtime-failure": 1,
      "setup-environment-failure": 1,
      "verifier-contract-mismatch": 1,
    });
    expect(report.diagnostics[1]?.failureExcerpts.join("\n")).not.toContain("main branch content");
    expect(report.diagnostics[1]?.failureExcerpts.join("\n")).not.toContain("custom content");
  });

  test("derives sanitized operational memory candidates from actionable diagnostics", () => {
    const report = buildExternalEvalDiagnosticReport({
      runId: "tb-run-1",
      createdAt: "2026-05-07T15:00:00.000Z",
      trials,
      evidence: [
        {
          trialId: "git-multibranch.1-of-1.tb-run-1",
          evidenceRefs: ["git-multibranch/sessions/tests.log"],
          verifierOutput: "error: src refspec main does not match any",
        },
        {
          trialId: "nginx-request-logging.1-of-1.tb-run-1",
          evidenceRefs: ["nginx-request-logging/sessions/tests.log"],
          verifierOutput: "AssertionError: Custom log format is missing required fields",
        },
        {
          trialId: "polyglot-c-py.1-of-1.tb-run-1",
          evidenceRefs: ["polyglot-c-py/results.json"],
        },
      ],
    });

    const pack = buildOperationalMemoryPackFromDiagnostics({
      packId: "tb-run-1-operational-checklists",
      version: "1.0.0",
      createdAt: "2026-05-07T15:05:00.000Z",
      report,
    });

    expect(pack.status).toBe("sanitized");
    expect(pack.integrity).toMatchObject({ status: "clean" });
    expect(pack.findings).toHaveLength(2);
    expect(pack.findings.map((finding) => finding.id)).toEqual([
      "tb-run-1-setup-environment-failure",
      "tb-run-1-verifier-contract-mismatch",
    ]);
    expect(pack.findings.every((finding) => finding.containsTaskAnswer === false)).toBe(true);
    expect(pack.findings.every((finding) => finding.containsSecret === false)).toBe(true);
    expect(pack.findings.map((finding) => finding.targetFamilies).flat()).toContain("terminal");
    expect(validateOperationalMemoryPack(pack)).toEqual({ valid: true });
  });

  test("classifies adapter crash evidence as adapter-runtime failure", () => {
    const report = buildExternalEvalDiagnosticReport({
      runId: "tb-run-1",
      createdAt: "2026-05-07T15:10:00.000Z",
      trials: [
        {
          taskId: "adapter-crash-task",
          trialId: "adapter-crash-task.1-of-1.tb-run-1",
          attempt: 1,
          status: "infrastructure-error",
          errorKind: "adapter-crash",
        },
      ],
      evidence: [
        {
          trialId: "adapter-crash-task.1-of-1.tb-run-1",
          evidenceRefs: ["adapter-crash-task/agent-logs/host-codex-stderr.txt"],
          adapterLifecycle: {
            runId: "tb-run-1",
            taskId: "adapter-crash-task",
            trialId: "adapter-crash-task.1-of-1.tb-run-1",
            adapter: "host-codex-docker",
            command: { argv: ["codex", "exec"], cwd: "/tmp" },
            status: "failed",
            errorKind: "adapter-crash",
            exitCode: 1,
            artifacts: {
              stdoutPath: "agent-logs/host-codex-stdout.txt",
              stderrPath: "agent-logs/host-codex-stderr.txt",
            },
          },
        },
      ],
    });

    expect(report.diagnostics).toHaveLength(1);
    expect(report.diagnostics[0]).toMatchObject({
      category: "adapter-runtime-failure",
      confidence: 0.95,
    });
    expect(report.diagnostics[0]?.failureExcerpts).toContain("adapter_error_kind=adapter-crash");
    expect(report.summary.countsByCategory).toEqual({
      "adapter-runtime-failure": 1,
    });
  });

  test("keeps discarded integrity-risk trials visible in diagnostics", () => {
    const report = buildExternalEvalDiagnosticReport({
      runId: "tb-run-1",
      createdAt: "2026-05-07T15:15:00.000Z",
      trials: [
        {
          taskId: "contaminated-task",
          trialId: "contaminated-task.1-of-1.tb-run-1",
          attempt: 1,
          status: "discarded",
          errorKind: "contaminated",
          notes: ["integrity_status=contaminated"],
        },
      ],
      evidence: [
        {
          trialId: "contaminated-task.1-of-1.tb-run-1",
          evidenceRefs: ["contaminated-task/results.json"],
        },
      ],
    });

    expect(report.diagnostics).toHaveLength(1);
    expect(report.diagnostics[0]).toMatchObject({
      category: "integrity-risk",
      confidence: 0.9,
    });
    expect(report.diagnostics[0]?.failureExcerpts).toContain("integrity_status=contaminated");
    expect(report.summary).toMatchObject({
      unresolvedTrials: 1,
      countsByCategory: { "integrity-risk": 1 },
    });
  });
});
