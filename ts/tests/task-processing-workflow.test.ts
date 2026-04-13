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
});
