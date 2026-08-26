import { describe, expect, it, vi } from "vitest";

import {
  executeStatusCommandWorkflow,
  getQueueUsageExitCode,
  planQueueCommand,
  QUEUE_HELP_TEXT,
  renderQueuedTaskResult,
  renderStatusResult,
} from "../src/cli/queue-status-command-workflow.js";
import type { AgentTaskSpec } from "../src/scenarios/agent-task-spec.js";
import {
  NATIVE_AGENT_TASK_QUEUE_MARKER,
  savedAgentTaskSpecDigest,
} from "../src/scenarios/saved-agent-task-routing.js";

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

  it("keeps structured specs authoritative instead of flattening away their private context", () => {
    const nativeSpec: AgentTaskSpec = {
      improvementTaskContractVersion: 1,
      taskPrompt: "Raw prompt",
      judgeRubric: "Saved rubric",
      outputFormat: "free_text",
      judgeModel: "",
      referenceContext: "VISIBLE_REFERENCE",
      evaluationContext: "EVALUATOR_ONLY_CASE",
      maxRounds: 3,
      qualityThreshold: 0.9,
    };
    const plan = planQueueCommand(
      {
        spec: "structured-scenario",
        prompt: undefined,
        rubric: undefined,
        "browser-url": undefined,
        priority: "1",
        "min-rounds": undefined,
        rlm: false,
      },
      {
        taskPrompt: "Rendered prompt with visible data",
        rubric: "Saved rubric",
        referenceContext: "VISIBLE_REFERENCE",
        requiredConcepts: ["accuracy"],
        maxRounds: 3,
        qualityThreshold: 0.9,
        agentTaskSpec: nativeSpec,
      },
    );

    expect(plan.request).toMatchObject({
      priority: 1,
      rlmEnabled: false,
      nativeTaskMarker: NATIVE_AGENT_TASK_QUEUE_MARKER,
      savedSpecDigest: savedAgentTaskSpecDigest(nativeSpec),
    });
    expect(plan.request.savedSpecDigest).toMatch(/^sha256:[0-9a-f]{64}$/);
    expect(plan.request.taskPrompt).toBeUndefined();
    expect(plan.request.rubric).toBeUndefined();
    expect(plan.request.referenceContext).toBeUndefined();
    expect(JSON.stringify(plan.request)).not.toContain("EVALUATOR_ONLY_CASE");
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
    expect(JSON.parse(renderStatusResult({ pendingCount: 4 }))).toEqual({
      pending_count: 4,
      pendingCount: 4,
    });
  });
});
