import { describe, expect, it, vi } from "vitest";

import {
  executeStatusCommandWorkflow,
  getQueueUsageExitCode,
  planQueueCommand,
  QUEUE_HELP_TEXT,
  renderQueuedTaskResult,
  renderStatusResult,
} from "../src/cli/queue-status-command-workflow.js";

describe("queue/status command workflow", () => {
  it("exposes stable queue help text", () => {
    expect(QUEUE_HELP_TEXT).toContain("autoctx queue");
    expect(QUEUE_HELP_TEXT).toContain("--priority");
    expect(QUEUE_HELP_TEXT).toContain("--rlm");
    expect(QUEUE_HELP_TEXT).toContain("--browser-url");
  });

  it("returns the right queue usage exit code", () => {
    expect(getQueueUsageExitCode(true)).toBe(0);
    expect(getQueueUsageExitCode(false)).toBe(1);
  });

  it("plans queue requests with saved scenario defaults and overrides", () => {
    expect(
      planQueueCommand(
        {
          spec: "saved-scenario",
          prompt: "override prompt",
          rubric: undefined,
          "browser-url": "https://status.example.com",
          priority: "2",
          "min-rounds": "3",
          rlm: true,
          "rlm-model": "claude",
          "rlm-turns": "7",
          "rlm-max-tokens": "2048",
          "rlm-temperature": "0.2",
          "rlm-max-stdout": "4096",
          "rlm-timeout-ms": "12000",
          "rlm-memory-mb": "128",
        },
        {
          taskPrompt: "saved prompt",
          rubric: "saved rubric",
          referenceContext: "saved context",
          requiredConcepts: ["concept-a"],
          maxRounds: 5,
          qualityThreshold: 0.8,
        },
      ),
    ).toEqual({
      specName: "saved-scenario",
      request: {
        taskPrompt: "override prompt",
        rubric: "saved rubric",
        browserUrl: "https://status.example.com",
        referenceContext: "saved context",
        requiredConcepts: ["concept-a"],
        maxRounds: 5,
        qualityThreshold: 0.8,
        priority: 2,
        minRounds: 3,
        rlmEnabled: true,
        rlmModel: "claude",
        rlmMaxTurns: 7,
        rlmMaxTokensPerTurn: 2048,
        rlmTemperature: 0.2,
        rlmMaxStdoutChars: 4096,
        rlmCodeTimeoutMs: 12000,
        rlmMemoryLimitMb: 128,
      },
    });
  });

  it("rejects queue requests without a spec", () => {
    expect(() =>
      planQueueCommand(
        {
          spec: undefined,
          prompt: undefined,
          rubric: undefined,
          "browser-url": undefined,
          priority: "0",
          "min-rounds": undefined,
          rlm: false,
          "rlm-model": undefined,
          "rlm-turns": undefined,
          "rlm-max-tokens": undefined,
          "rlm-temperature": undefined,
          "rlm-max-stdout": undefined,
          "rlm-timeout-ms": undefined,
          "rlm-memory-mb": undefined,
        },
        null,
      ),
    ).toThrow("Queue spec is required");
  });

  it("renders queued task payloads", () => {
    expect(renderQueuedTaskResult({ taskId: "task-123", specName: "saved-scenario" })).toBe(
      JSON.stringify({ taskId: "task-123", specName: "saved-scenario", status: "queued" }),
    );
  });

  it("executes status workflow and closes the store", () => {
    const migrate = vi.fn();
    const pendingTaskCount = vi.fn().mockReturnValue(4);
    const close = vi.fn();

    expect(
      executeStatusCommandWorkflow({
        store: { migrate, pendingTaskCount, close },
        migrationsDir: "/tmp/migrations",
      }),
    ).toEqual({ pendingCount: 4 });

    expect(migrate).toHaveBeenCalledWith("/tmp/migrations");
    expect(pendingTaskCount).toHaveBeenCalled();
    expect(close).toHaveBeenCalled();
  });

  it("renders status payloads", () => {
    expect(renderStatusResult({ pendingCount: 4 })).toBe(
      JSON.stringify({ pendingCount: 4 }),
    );
  });
});
