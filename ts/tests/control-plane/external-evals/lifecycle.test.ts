import { describe, expect, test } from "vitest";
import {
  classifyExternalEvalTrial,
  validateExternalEvalAdapterLifecycle,
} from "../../../src/control-plane/external-evals/index.js";

describe("external eval adapter lifecycle", () => {
  test("requires durable stdout and stderr paths for timed-out adapter runs", () => {
    const result = validateExternalEvalAdapterLifecycle({
      runId: "tb-run-1",
      taskId: "polyglot-c-py",
      trialId: "polyglot-c-py.1-of-1.tb-run-1",
      adapter: "host-codex-docker",
      command: {
        argv: ["codex", "exec", "--output-last-message", "host-codex-final.txt"],
        cwd: "/tmp",
      },
      status: "timed-out",
      timeoutSource: "global-agent-timeout",
      startedAt: "2026-05-07T04:41:32.967Z",
      endedAt: "2026-05-07T14:29:41.251Z",
      artifacts: {
        finalMessagePath: "agent-logs/host-codex-final.txt",
      },
    });

    expect(result).toMatchObject({ valid: false });
    if (!result.valid) {
      expect(result.errors).toContain("artifacts.stdoutPath must be a non-empty string");
      expect(result.errors).toContain("artifacts.stderrPath must be a non-empty string");
    }
  });

  test("classifies adapter timeouts as infrastructure errors instead of normal task failures", () => {
    const trial = classifyExternalEvalTrial({
      taskId: "polyglot-c-py",
      trialId: "polyglot-c-py.1-of-1.tb-run-1",
      attempt: 1,
      isResolved: false,
      failureMode: "agent_timeout",
      rawResultPath: "polyglot-c-py/results.json",
      lifecycle: {
        runId: "tb-run-1",
        taskId: "polyglot-c-py",
        trialId: "polyglot-c-py.1-of-1.tb-run-1",
        adapter: "host-codex-docker",
        command: {
          argv: ["codex", "exec"],
          cwd: "/tmp",
        },
        status: "timed-out",
        timeoutSource: "global-agent-timeout",
        startedAt: "2026-05-07T04:41:32.967Z",
        endedAt: "2026-05-07T14:29:41.251Z",
        artifacts: {
          stdoutPath: "agent-logs/host-codex-stdout.txt",
          stderrPath: "agent-logs/host-codex-stderr.txt",
          finalMessagePath: "agent-logs/host-codex-final.txt",
          tokens: { input: 0, output: 0 },
        },
      },
    });

    expect(trial).toMatchObject({
      taskId: "polyglot-c-py",
      status: "infrastructure-error",
      errorKind: "agent_timeout",
      rawResultPath: "polyglot-c-py/results.json",
    });
    expect(trial.notes).toContain("failure_mode=agent_timeout");
    expect(trial.notes).toContain("adapter_status=timed-out");
    expect(trial.notes).toContain("timeout_source=global-agent-timeout");
  });

  test("classifies unresolved verifier results without adapter failure as task failures", () => {
    const trial = classifyExternalEvalTrial({
      taskId: "nginx-request-logging",
      trialId: "nginx-request-logging.1-of-1.tb-run-1",
      attempt: 1,
      isResolved: false,
      failureMode: "unset",
    });

    expect(trial.status).toBe("failed");
    expect(trial.errorKind).toBeUndefined();
  });
});
