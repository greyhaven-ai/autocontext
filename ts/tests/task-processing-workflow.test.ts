import { describe, expect, it, vi } from "vitest";

import {
  buildQueuedTaskExecutionPlan,
  executeQueuedTaskWorkflow,
} from "../src/execution/task-processing-workflow.js";
import type { AgentTaskSpec } from "../src/scenarios/agent-task-spec.js";
import {
  NATIVE_AGENT_TASK_QUEUE_MARKER,
  savedAgentTaskSpecDigest,
} from "../src/scenarios/saved-agent-task-routing.js";

function createStructuredSpec(overrides: Partial<AgentTaskSpec> = {}): AgentTaskSpec {
  return {
    improvementTaskContractVersion: 1,
    taskPrompt: "Raw candidate prompt",
    judgeRubric: "Saved rubric",
    outputFormat: "free_text",
    judgeModel: "",
    referenceContext: "VISIBLE_REFERENCE",
    evaluationContext: "EVALUATOR_ONLY_CASE",
    requiredConcepts: ["accuracy"],
    maxRounds: 3,
    qualityThreshold: 0.9,
    ...overrides,
  };
}

function nativeQueueConfig(
  spec: AgentTaskSpec,
  extra: Record<string, unknown> = {},
): string {
  return JSON.stringify({
    ...extra,
    native_task_marker: NATIVE_AGENT_TASK_QUEUE_MARKER,
    saved_spec_digest: savedAgentTaskSpecDigest(spec),
  });
}

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
      candidateGrounding: false,
    });
    expect(plan.delegatedJudge).toEqual({ tag: "judge" });
  });

  it("keeps legacy saved-spec references judge-only during queued generation", async () => {
    const generateOutput = vi.fn(async () => "generated output");
    const run = vi.fn(async () => ({
      rounds: [],
      bestOutput: "best output",
      bestScore: 0.8,
      bestRound: 1,
      totalRounds: 1,
      metThreshold: false,
      judgeFailures: 0,
      terminationReason: "max_rounds",
      dimensionTrajectory: {},
      totalInternalRetries: 0,
      durationMs: 1,
      judgeCalls: 1,
    }));
    const createAgentTask = vi.fn(() => ({
      initialState: () => ({}),
      generateOutput,
      getRlmSessions: () => [],
    }));

    await executeQueuedTaskWorkflow({
      store: { completeTask: vi.fn(), failTask: vi.fn() } as never,
      task: {
        id: "legacy-saved",
        spec_name: "legacy-saved",
        config_json: JSON.stringify({}),
      } as never,
      provider: { complete: vi.fn(), defaultModel: () => "mock", name: "mock" } as never,
      model: "candidate-model",
      knowledgeRoot: "/knowledge",
      internals: {
        resolveSavedTask: () => ({
          name: "legacy-saved",
          spec: {
            taskPrompt: "Legacy public task",
            judgeRubric: "Legacy judge rubric",
            referenceContext: "JUDGE_ONLY_REFERENCE",
            requiredConcepts: ["JUDGE_ONLY_CONCEPT"],
          },
        }),
        createAgentTask: createAgentTask as never,
        createImprovementLoop: vi.fn(() => ({ run })) as never,
      },
    });

    expect(createAgentTask).toHaveBeenCalledWith(expect.objectContaining({
      taskPrompt: "Legacy public task",
      candidateGrounding: false,
    }));
    expect(generateOutput).toHaveBeenCalledWith({
      referenceContext: undefined,
      requiredConcepts: undefined,
    });
    expect(run).toHaveBeenCalledWith(expect.objectContaining({
      referenceContext: "JUDGE_ONLY_REFERENCE",
      requiredConcepts: ["JUDGE_ONLY_CONCEPT"],
    }));
  });

  it("preserves a saved structured task marker and evaluator-only context in the native plan", () => {
    const structuredSpec = createStructuredSpec();
    const plan = buildQueuedTaskExecutionPlan({
      task: {
        spec_name: "structured-task",
        config_json: nativeQueueConfig(structuredSpec, {
          // Older queued rows flattened this rendered prompt into config.
          task_prompt: "Rendered candidate prompt",
        }),
      },
      knowledgeRoot: "/knowledge",
      internals: {
        renderSavedTaskPrompt: () => "Rendered candidate prompt",
        resolveSavedTask: () => ({
          name: "structured-task",
          spec: structuredSpec,
        }),
      },
    });

    expect(plan.taskPrompt).toBe("Raw candidate prompt");
    expect(plan.nativeAgentTask).toMatchObject({
      name: "structured-task",
      spec: {
        improvementTaskContractVersion: 1,
        taskPrompt: "Raw candidate prompt",
        referenceContext: "VISIBLE_REFERENCE",
        evaluationContext: "EVALUATOR_ONLY_CASE",
      },
    });
  });

  it("fails closed when queued native metadata cannot reload the saved spec", () => {
    const structuredSpec = createStructuredSpec();
    const task = {
      spec_name: "structured-task",
      config_json: nativeQueueConfig(structuredSpec),
    };

    expect(() => buildQueuedTaskExecutionPlan({ task })).toThrow(
      "requires a knowledge root to reload its saved spec",
    );
    expect(() => buildQueuedTaskExecutionPlan({
      task,
      knowledgeRoot: "/knowledge",
      internals: { resolveSavedTask: () => null },
    })).toThrow("saved spec is missing");
  });

  it.each([
    {
      native_task_marker: NATIVE_AGENT_TASK_QUEUE_MARKER,
    },
    {
      saved_spec_digest: `sha256:${"a".repeat(64)}`,
    },
  ])("rejects incomplete queued native metadata", (config) => {
    expect(() => buildQueuedTaskExecutionPlan({
      task: {
        spec_name: "structured-task",
        config_json: JSON.stringify(config),
      },
    })).toThrow("has incomplete immutable saved-spec metadata");
  });

  it("fails closed when a queued native digest no longer matches the saved spec", () => {
    const currentSpec = createStructuredSpec({ taskPrompt: "Current prompt" });
    const queuedSpec = createStructuredSpec({ taskPrompt: "Original queued prompt" });

    expect(() => buildQueuedTaskExecutionPlan({
      task: {
        spec_name: "structured-task",
        config_json: nativeQueueConfig(queuedSpec),
      },
      knowledgeRoot: "/knowledge",
      internals: {
        resolveSavedTask: () => ({ name: "structured-task", spec: currentSpec }),
      },
    })).toThrow("saved spec digest does not match the immutable queued digest");
  });

  it("rejects a native saved task from a legacy queue row without immutable metadata", () => {
    const structuredSpec = createStructuredSpec();

    expect(() => buildQueuedTaskExecutionPlan({
      task: {
        spec_name: "structured-task",
        config_json: JSON.stringify({}),
      },
      knowledgeRoot: "/knowledge",
      internals: {
        resolveSavedTask: () => ({ name: "structured-task", spec: structuredSpec }),
      },
    })).toThrow("missing immutable saved-spec metadata; enqueue it again");
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

  it("executes persisted structured tasks through the native dual-context workflow", async () => {
    const structuredSpec = createStructuredSpec({ maxRounds: 2 });
    const completeTask = vi.fn();
    const failTask = vi.fn();
    const generateOutput = vi.fn(async () => "generated output");
    const nativeTask = {
      getTaskPrompt: () => "Raw candidate prompt",
      getRubric: () => "Saved rubric",
      describeTask: () => "Raw candidate prompt",
      initialState: () => ({ structured: true }),
      prepareContext: vi.fn(async (state: Record<string, unknown>) => ({ ...state, ready: true })),
      validateContext: vi.fn(() => []),
      evaluateOutput: vi.fn(),
      reviseOutput: vi.fn(),
      generateOutput,
      getRlmSessions: () => [],
    };
    const createStructuredAgentTask = vi.fn(() => nativeTask);
    const run = vi.fn(async () => ({
      rounds: [{
        roundNumber: 1,
        output: "best output",
        score: 0.93,
        reasoning: "Valid authoritative verdict",
        dimensionScores: { accuracy: 0.93 },
        isRevision: false,
        judgeFailed: false,
        evaluatorEpoch: null,
      }],
      bestOutput: "best output",
      bestScore: 0.93,
      bestRound: 1,
      totalRounds: 1,
      metThreshold: true,
      judgeFailures: 0,
      terminationReason: "threshold_met",
      dimensionTrajectory: {},
      totalInternalRetries: 0,
      durationMs: 10,
      judgeCalls: 1,
    }));

    await executeQueuedTaskWorkflow({
      store: { completeTask, failTask } as never,
      task: {
        id: "structured-queue-task",
        spec_name: "structured-task",
        config_json: nativeQueueConfig(structuredSpec),
      } as never,
      provider: { complete: vi.fn(), defaultModel: () => "mock", name: "mock" } as never,
      model: "worker-model",
      knowledgeRoot: "/knowledge",
      internals: {
        resolveSavedTask: () => ({
          name: "structured-task",
          spec: structuredSpec,
        }),
        createAgentTask: vi.fn(() => {
          throw new Error("generic task must not be created");
        }) as never,
        createStructuredAgentTask: createStructuredAgentTask as never,
        createImprovementLoop: vi.fn(() => ({ run })) as never,
        serializeTaskResult: vi.fn(() => "serialized-result"),
      },
    });

    expect(createStructuredAgentTask).toHaveBeenCalledWith({
      name: "structured-task",
      spec: expect.objectContaining({
        improvementTaskContractVersion: 1,
        evaluationContext: "EVALUATOR_ONLY_CASE",
        referenceContext: "VISIBLE_REFERENCE",
      }),
      provider: expect.objectContaining({ name: "mock" }),
      model: "worker-model",
    });
    expect(generateOutput).toHaveBeenCalledWith({
      referenceContext: "VISIBLE_REFERENCE",
      requiredConcepts: ["accuracy"],
      state: { structured: true, ready: true },
    });
    expect(run).toHaveBeenCalledWith({
      initialOutput: "generated output",
      state: { structured: true, ready: true },
      referenceContext: "VISIBLE_REFERENCE",
      requiredConcepts: ["accuracy"],
      calibrationExamples: undefined,
    });
    expect(completeTask).toHaveBeenCalled();
    expect(failTask).not.toHaveBeenCalled();
  });

  it("completes a native queue run after a transient failed authoritative round", async () => {
    const structuredSpec = createStructuredSpec({ maxRounds: 2 });
    const completeTask = vi.fn();
    const failTask = vi.fn();
    const run = vi.fn(async () => ({
      rounds: [
        {
          roundNumber: 1,
          output: "draft",
          score: 0,
          reasoning: "Authoritative response could not be parsed",
          dimensionScores: {},
          isRevision: false,
          judgeFailed: true,
          evaluatorEpoch: null,
        },
        {
          roundNumber: 2,
          output: "recovered",
          score: 0.94,
          reasoning: "Valid authoritative verdict",
          dimensionScores: { accuracy: 0.94 },
          isRevision: true,
          judgeFailed: false,
          evaluatorEpoch: "epoch-1",
        },
      ],
      bestOutput: "recovered",
      bestScore: 0.94,
      bestRound: 2,
      totalRounds: 2,
      metThreshold: true,
      judgeFailures: 1,
      terminationReason: "threshold_met",
      dimensionTrajectory: { accuracy: [0.94] },
      totalInternalRetries: 0,
      durationMs: 10,
      judgeCalls: 2,
      evaluatorEpoch: "epoch-1",
    }));

    await executeQueuedTaskWorkflow({
      store: { completeTask, failTask } as never,
      task: {
        id: "native-recovered",
        spec_name: "structured-task",
        config_json: nativeQueueConfig(structuredSpec, { initial_output: "draft" }),
      } as never,
      provider: { complete: vi.fn(), defaultModel: () => "mock", name: "mock" } as never,
      model: "worker-model",
      knowledgeRoot: "/knowledge",
      internals: {
        resolveSavedTask: () => ({ name: "structured-task", spec: structuredSpec }),
        createStructuredAgentTask: vi.fn(() => ({
          initialState: () => ({}),
          generateOutput: vi.fn(),
          getRlmSessions: () => [],
        })) as never,
        createImprovementLoop: vi.fn(() => ({ run })) as never,
      },
    });

    expect(failTask).not.toHaveBeenCalled();
    expect(completeTask).toHaveBeenCalledOnce();
    const resultPayload = JSON.parse(completeTask.mock.calls[0][5]);
    expect(resultPayload.rounds.map((round: { judge_failed: boolean }) => round.judge_failed))
      .toEqual([true, false]);
    expect(resultPayload.judge_failures).toBe(1);
    expect(resultPayload.termination_reason).toBe("threshold_met");
  });

  it("retries a native queue run with no usable authoritative evaluation", async () => {
    const structuredSpec = createStructuredSpec({ maxRounds: 1 });
    const completeTask = vi.fn();
    const failTask = vi.fn();
    const run = vi.fn(async () => ({
      rounds: [{
        roundNumber: 1,
        output: "draft",
        score: 0,
        reasoning: "Authoritative response could not be parsed",
        dimensionScores: {},
        isRevision: false,
        judgeFailed: true,
        evaluatorEpoch: null,
      }],
      bestOutput: "draft",
      bestScore: 0,
      bestRound: 1,
      totalRounds: 1,
      metThreshold: false,
      judgeFailures: 1,
      terminationReason: "max_rounds",
      dimensionTrajectory: {},
      totalInternalRetries: 0,
      durationMs: 10,
      judgeCalls: 1,
      evaluatorEpoch: null,
    }));

    await executeQueuedTaskWorkflow({
      store: { completeTask, failTask } as never,
      maxAttempts: 3,
      task: {
        id: "native-unusable",
        spec_name: "structured-task",
        config_json: nativeQueueConfig(structuredSpec, { initial_output: "draft" }),
      } as never,
      provider: { complete: vi.fn(), defaultModel: () => "mock", name: "mock" } as never,
      model: "worker-model",
      knowledgeRoot: "/knowledge",
      internals: {
        resolveSavedTask: () => ({ name: "structured-task", spec: structuredSpec }),
        createStructuredAgentTask: vi.fn(() => ({
          initialState: () => ({}),
          generateOutput: vi.fn(),
          getRlmSessions: () => [],
        })) as never,
        createImprovementLoop: vi.fn(() => ({ run })) as never,
      },
    });

    expect(completeTask).not.toHaveBeenCalled();
    expect(failTask).toHaveBeenCalledWith(
      "native-unusable",
      "Queued native task 'structured-task' produced no usable authoritative evaluation "
        + "(judge_failures=1, total_rounds=1, termination_reason=max_rounds)",
      3,
    );
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
