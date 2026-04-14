import { describe, expect, it, vi } from "vitest";

import { registerCoreControlPlaneTools } from "../src/mcp/core-control-tools.js";

function createFakeServer() {
  const registeredTools: Record<
    string,
    {
      description: string;
      schema: Record<string, unknown>;
      handler: (args: Record<string, unknown>) => Promise<{ content: Array<{ type: string; text: string }> }>;
    }
  > = {};

  return {
    registeredTools,
    tool(
      name: string,
      description: string,
      schema: Record<string, unknown>,
      handler: (args: Record<string, unknown>) => Promise<{ content: Array<{ type: string; text: string }> }>,
    ) {
      registeredTools[name] = { description, schema, handler };
    },
  };
}

function createProvider() {
  return {
    name: "mock",
    defaultModel: () => "mock",
    complete: async () => ({ text: "ok", usage: {} }),
  };
}

describe("core control plane MCP tools", () => {
  it("registers delegated evaluation and returns delegated payloads", async () => {
    const server = createFakeServer();
    const createJudge = vi.fn(() => ({
      evaluate: async () => ({ score: 0.1, reasoning: "non-delegated" }),
    }));
    const createDelegatedJudge = vi.fn(() => ({
      evaluate: async () => ({
        score: 0.82,
        reasoning: "delegated",
        dimensionScores: { clarity: 0.82 },
      }),
    }));

    registerCoreControlPlaneTools(server, {
      store: {
        pendingTaskCount: () => 0,
        getTask: () => null,
      },
      provider: createProvider(),
      internals: {
        createJudge,
        createDelegatedJudge,
      },
    });

    const result = await server.registeredTools.evaluate_output.handler({
      taskPrompt: "Summarize",
      agentOutput: "Draft",
      rubric: "Score clarity",
      delegatedResult: {
        score: 0.82,
        reasoning: "delegated",
        dimensionScores: { clarity: 0.82 },
      },
    });

    expect(createJudge).not.toHaveBeenCalled();
    expect(createDelegatedJudge).toHaveBeenCalledOnce();
    expect(JSON.parse(result.content[0].text)).toEqual({
      score: 0.82,
      reasoning: "delegated",
      dimensionScores: { clarity: 0.82 },
    });
  });

  it("registers improvement-loop tool with injected task and loop workflows", async () => {
    const server = createFakeServer();
    const generateOutput = vi.fn(async () => "generated draft");
    const task = {
      generateOutput,
      getRlmSessions: () => [{ id: "rlm-1" }],
    };
    const run = vi.fn(async () => ({
      totalRounds: 2,
      metThreshold: true,
      bestScore: 0.93,
      bestRound: 2,
      judgeFailures: 0,
      rounds: [
        {
          roundNumber: 1,
          score: 0.75,
          isRevision: false,
          judgeFailed: false,
          reasoning: "a".repeat(220),
        },
      ],
      bestOutput: "b".repeat(600),
    }));

    registerCoreControlPlaneTools(server, {
      store: {
        pendingTaskCount: () => 0,
        getTask: () => null,
      },
      provider: createProvider(),
      internals: {
        createSequentialDelegatedJudge: vi.fn(() => ({ delegated: true })),
        createAgentTask: vi.fn(() => task),
        createImprovementLoop: vi.fn(() => ({ run })),
      },
    });

    const result = await server.registeredTools.run_improvement_loop.handler({
      taskPrompt: "Write a summary",
      rubric: "Score clarity",
      maxRounds: 2,
      qualityThreshold: 0.9,
    });

    const payload = JSON.parse(result.content[0].text);
    expect(generateOutput).toHaveBeenCalledOnce();
    expect(run).toHaveBeenCalledOnce();
    expect(payload.bestScore).toBe(0.93);
    expect(payload.rounds[0].reasoningPreview.length).toBe(200);
    expect(payload.bestOutputPreview.length).toBe(500);
    expect(payload.rlmSessions).toEqual([{ id: "rlm-1" }]);
  });

  it("returns structured revise errors without invoking the RLM session runner", async () => {
    const server = createFakeServer();
    const runReplSession = vi.fn();

    registerCoreControlPlaneTools(server, {
      store: {
        pendingTaskCount: () => 0,
        getTask: () => null,
      },
      provider: createProvider(),
      internals: {
        runReplSession,
      },
    });

    const result = await server.registeredTools.run_repl_session.handler({
      taskPrompt: "Explain testing",
      rubric: "Be clear",
      phase: "revise",
    });

    expect(runReplSession).not.toHaveBeenCalled();
    expect(JSON.parse(result.content[0].text)).toEqual({
      error: "currentOutput is required when phase=revise",
    });
  });

  it("registers queue and capabilities tools with stable payload shapes", async () => {
    const server = createFakeServer();
    const enqueueTask = vi.fn(() => "task-123");

    registerCoreControlPlaneTools(server, {
      store: {
        pendingTaskCount: () => 4,
        getTask: (taskId: string) => taskId === "task-123"
          ? {
              id: "task-123",
              spec_name: "spec-a",
              status: "completed",
              priority: 2,
              config_json: null,
              scheduled_at: null,
              started_at: null,
              completed_at: "2026-04-10T00:00:00.000Z",
              best_score: 0.91,
              best_output: "Best output",
              total_rounds: 3,
              met_threshold: 1,
              result_json: null,
              error: null,
              created_at: "2026-04-10T00:00:00.000Z",
              updated_at: "2026-04-10T00:00:00.000Z",
            }
          : null,
      },
      provider: createProvider(),
      internals: {
        enqueueTask,
        getCapabilities: () => ({ commands: ["capabilities", "mission", "campaign"] }),
      },
    });

    const queued = await server.registeredTools.queue_task.handler({
      specName: "spec-a",
      priority: 2,
    });
    expect(JSON.parse(queued.content[0].text)).toEqual({
      taskId: "task-123",
      specName: "spec-a",
      status: "queued",
    });

    const queueStatus = await server.registeredTools.get_queue_status.handler({});
    expect(JSON.parse(queueStatus.content[0].text)).toEqual({ pendingCount: 4 });

    const taskResult = await server.registeredTools.get_task_result.handler({ taskId: "task-123" });
    expect(JSON.parse(taskResult.content[0].text)).toEqual({
      id: "task-123",
      specName: "spec-a",
      status: "completed",
      priority: 2,
      createdAt: "2026-04-10T00:00:00.000Z",
      bestScore: 0.91,
      totalRounds: 3,
      metThreshold: true,
      bestOutput: "Best output",
      completedAt: "2026-04-10T00:00:00.000Z",
    });

    const capabilities = await server.registeredTools.capabilities.handler({});
    expect(JSON.parse(capabilities.content[0].text)).toEqual({
      commands: ["capabilities", "mission", "campaign"],
    });
  });
});
