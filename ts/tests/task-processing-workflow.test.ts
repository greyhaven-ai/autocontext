import { describe, expect, it, vi } from "vitest";

import {
  buildQueuedTaskExecutionPlan,
  executeQueuedTaskWorkflow,
} from "../src/execution/task-processing-workflow.js";

describe("task processing workflow", () => {
  it("merges explicit queue config, saved task defaults, and fallback defaults", () => {
    const plan = buildQueuedTaskExecutionPlan({
      task: {
        spec_name: "saved-task",
        config_json: JSON.stringify({
          task_prompt: "Queued prompt",
          min_rounds: 3,
          browser_url: "https://example.com",
          delegated_results: [{ score: 0.8, reasoning: "delegated" }],
        }),
      },
      knowledgeRoot: "/knowledge",
      internals: {
        resolveSavedTask: () => ({
          spec: {
            judgeRubric: "Saved rubric",
            referenceContext: "Saved context",
            requiredConcepts: ["clarity"],
            maxRounds: 7,
            qualityThreshold: 0.95,
            revisionPrompt: "Saved revision",
          },
        }),
        createDelegatedJudge: vi.fn(() => ({ tag: "judge" })) as never,
      },
    });

    expect(plan).toMatchObject({
      taskPrompt: "Queued prompt",
      rubric: "Saved rubric",
      referenceContext: "Saved context",
      requiredConcepts: ["clarity"],
      browserUrl: "https://example.com",
      maxRounds: 7,
      qualityThreshold: 0.95,
      minRounds: 3,
      revisionPrompt: "Saved revision",
    });
    expect(plan.delegatedJudge).toEqual({ tag: "judge" });
  });

  it("completes tasks through injected agent/loop workflows", async () => {
    const completeTask = vi.fn();
    const failTask = vi.fn();
    const generateOutput = vi.fn(async () => "generated output");
    const run = vi.fn(async () => ({
      rounds: [],
      bestOutput: "best output",
      bestScore: 0.92,
      bestRound: 2,
      totalRounds: 2,
      metThreshold: true,
      judgeFailures: 0,
      terminationReason: "threshold_met",
      dimensionTrajectory: {},
      totalInternalRetries: 0,
      durationMs: 10,
      judgeCalls: 2,
    }));

    await executeQueuedTaskWorkflow({
      store: { completeTask, failTask } as never,
      task: {
        id: "task-1",
        spec_name: "queued-spec",
        config_json: JSON.stringify({ task_prompt: "Prompt" }),
      } as never,
      provider: { complete: vi.fn(), defaultModel: () => "mock", name: "mock" } as never,
      model: "mock-model",
      internals: {
        createAgentTask: vi.fn(() => ({
          initialState: () => ({ seed: 1 }),
          generateOutput,
          getRlmSessions: () => [{ phase: "generate", content: "generated output" }],
        })) as never,
        createImprovementLoop: vi.fn(() => ({ run })) as never,
        serializeTaskResult: vi.fn(() => "serialized-result"),
      },
    });

    expect(generateOutput).toHaveBeenCalledOnce();
    expect(run).toHaveBeenCalledWith({
      initialOutput: "generated output",
      state: { seed: 1 },
      referenceContext: undefined,
      requiredConcepts: undefined,
      calibrationExamples: undefined,
    });
    expect(completeTask).toHaveBeenCalledWith(
      "task-1",
      0.92,
      "best output",
      2,
      true,
      "serialized-result",
    );
    expect(failTask).not.toHaveBeenCalled();
  });

  it("fails tasks with message-only errors when planning or execution throws", async () => {
    const completeTask = vi.fn();
    const failTask = vi.fn();

    await executeQueuedTaskWorkflow({
      store: { completeTask, failTask } as never,
      task: {
        id: "task-2",
        spec_name: "queued-spec",
        config_json: JSON.stringify({ task_prompt: "Prompt" }),
      } as never,
      provider: { complete: vi.fn(), defaultModel: () => "mock", name: "mock" } as never,
      model: "mock-model",
      internals: {
        createAgentTask: vi.fn(() => {
          throw new Error("workflow exploded");
        }) as never,
      },
    });

    expect(completeTask).not.toHaveBeenCalled();
    expect(failTask).toHaveBeenCalledWith("task-2", "workflow exploded");
  });

  it("captures browser context and merges it into the authoritative reference context", async () => {
    const completeTask = vi.fn();
    const failTask = vi.fn();
    const generateOutput = vi.fn(async () => "generated output");
    const run = vi.fn(async () => ({
      rounds: [],
      bestOutput: "best output",
      bestScore: 0.92,
      bestRound: 2,
      totalRounds: 2,
      metThreshold: true,
      judgeFailures: 0,
      terminationReason: "threshold_met",
      dimensionTrajectory: {},
      totalInternalRetries: 0,
      durationMs: 10,
      judgeCalls: 2,
    }));
    const mergedReferenceContext = [
      "Saved context",
      "Live browser context:",
      "URL: https://status.example.com",
      "Title: Status page",
      "Visible text: All systems operational",
    ].join("\n");
    const browserContextService = {
      buildReferenceContext: vi.fn(async () => mergedReferenceContext),
    };

    await executeQueuedTaskWorkflow({
      store: { completeTask, failTask } as never,
      task: {
        id: "task-browser",
        spec_name: "queued-spec",
        config_json: JSON.stringify({
          task_prompt: "Prompt",
          reference_context: "Saved context",
          browser_url: "https://status.example.com",
        }),
      } as never,
      provider: { complete: vi.fn(), defaultModel: () => "mock", name: "mock" } as never,
      model: "mock-model",
      browserContextService: browserContextService as never,
      internals: {
        createAgentTask: vi.fn(() => ({
          initialState: () => ({ seed: 1 }),
          generateOutput,
          getRlmSessions: () => [],
        })) as never,
        createImprovementLoop: vi.fn(() => ({ run })) as never,
        serializeTaskResult: vi.fn(() => "serialized-result"),
      },
    });

    expect(browserContextService.buildReferenceContext).toHaveBeenCalledWith({
      taskId: "task-browser",
      browserUrl: "https://status.example.com",
      referenceContext: "Saved context",
    });
    expect(generateOutput).toHaveBeenCalledWith({
      referenceContext: mergedReferenceContext,
      requiredConcepts: undefined,
    });
    expect(run).toHaveBeenCalledWith({
      initialOutput: "generated output",
      state: { seed: 1 },
      referenceContext: mergedReferenceContext,
      requiredConcepts: undefined,
      calibrationExamples: undefined,
    });
    expect(failTask).not.toHaveBeenCalled();
  });

  it("fails closed when queued browser context is requested without a service", async () => {
    const completeTask = vi.fn();
    const failTask = vi.fn();

    await executeQueuedTaskWorkflow({
      store: { completeTask, failTask } as never,
      task: {
        id: "task-browser-disabled",
        spec_name: "queued-spec",
        config_json: JSON.stringify({
          task_prompt: "Prompt",
          browser_url: "https://status.example.com",
        }),
      } as never,
      provider: { complete: vi.fn(), defaultModel: () => "mock", name: "mock" } as never,
      model: "mock-model",
      internals: {
        createAgentTask: vi.fn(() => {
          throw new Error("agent should not be created");
        }) as never,
      },
    });

    expect(completeTask).not.toHaveBeenCalled();
    expect(failTask).toHaveBeenCalledWith(
      "task-browser-disabled",
      "browser exploration is not configured",
    );
  });
});
