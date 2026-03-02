import { describe, it, expect } from "vitest";
import { JudgeExecutor } from "../src/execution/judge-executor.js";
import type { AgentTaskInterface, AgentTaskResult } from "../src/types/index.js";

function makeTask(overrides?: Partial<AgentTaskInterface>): AgentTaskInterface {
  return {
    getTaskPrompt: () => "test prompt",
    getRubric: () => "test rubric",
    initialState: () => ({}),
    describeTask: () => "test task",
    evaluateOutput: async (output, _state, _opts) => ({
      score: output.includes("good") ? 0.9 : 0.3,
      reasoning: "test evaluation",
      dimensionScores: { quality: 0.8 },
    }),
    reviseOutput: async (output) => output,
    ...overrides,
  };
}

describe("JudgeExecutor", () => {
  it("delegates to task.evaluateOutput", async () => {
    const task = makeTask();
    const executor = new JudgeExecutor(task);
    const result = await executor.execute("good output", {});
    expect(result.score).toBe(0.9);
    expect(result.reasoning).toBe("test evaluation");
    expect(result.dimensionScores.quality).toBe(0.8);
  });

  it("passes options through", async () => {
    let capturedOpts: Record<string, unknown> | undefined;
    const task = makeTask({
      evaluateOutput: async (_out, _state, opts) => {
        capturedOpts = opts as Record<string, unknown>;
        return { score: 0.5, reasoning: "ok", dimensionScores: {} };
      },
    });
    const executor = new JudgeExecutor(task);
    await executor.execute("output", {}, {
      referenceContext: "ref context",
      requiredConcepts: ["a", "b"],
    });
    expect(capturedOpts?.referenceContext).toBe("ref context");
    expect(capturedOpts?.requiredConcepts).toEqual(["a", "b"]);
  });

  it("runs context preparation when available", async () => {
    const task = makeTask({
      prepareContext: async (state) => ({ ...state, prepared: true }),
    });
    let capturedState: Record<string, unknown> = {};
    task.evaluateOutput = async (_out, state) => {
      capturedState = state;
      return { score: 0.7, reasoning: "prepared", dimensionScores: {} };
    };
    const executor = new JudgeExecutor(task);
    await executor.execute("output", { original: true });
    expect(capturedState.prepared).toBe(true);
    expect(capturedState.original).toBe(true);
  });

  it("fails with score 0 when context validation fails", async () => {
    const task = makeTask({
      validateContext: () => ["missing required field X", "missing required field Y"],
    });
    const executor = new JudgeExecutor(task);
    const result = await executor.execute("output", {});
    expect(result.score).toBe(0.0);
    expect(result.reasoning).toContain("Context validation failed");
    expect(result.reasoning).toContain("missing required field X");
    expect(result.reasoning).toContain("missing required field Y");
  });

  it("skips preparation/validation when not provided", async () => {
    // Task with no prepareContext or validateContext
    const task: AgentTaskInterface = {
      getTaskPrompt: () => "test",
      getRubric: () => "rubric",
      initialState: () => ({}),
      describeTask: () => "test",
      evaluateOutput: async () => ({ score: 0.6, reasoning: "basic", dimensionScores: {} }),
      reviseOutput: async (o) => o,
    };
    const executor = new JudgeExecutor(task);
    const result = await executor.execute("output", {});
    expect(result.score).toBe(0.6);
  });
});
