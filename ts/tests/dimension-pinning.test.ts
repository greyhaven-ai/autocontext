/**
 * Tests for dimension pinning across improvement loop rounds (AC-48).
 */
import { describe, it, expect } from "vitest";
import { LLMJudge } from "../src/judge/index.js";
import { ImprovementLoop } from "../src/execution/improvement-loop.js";
import { SimpleAgentTask } from "../src/execution/task-runner.js";
import type {
  LLMProvider,
  AgentTaskInterface,
  AgentTaskResult,
} from "../src/types/index.js";

function makeMockProvider(response: string): LLMProvider {
  return {
    name: "mock",
    defaultModel: () => "mock-model",
    complete: async () => ({ text: response, usage: {} }),
  };
}

const JUDGE_RESPONSE_WITH_DIMS =
  '<!-- JUDGE_RESULT_START -->' +
  '{"score": 0.7, "reasoning": "Decent", ' +
  '"dimensions": {"creativity": 0.8, "depth": 0.6}}' +
  '<!-- JUDGE_RESULT_END -->';

describe("Pinned dimensions in judge prompt", () => {
  it("includes required dimensions section when pinned", async () => {
    const provider = makeMockProvider(JUDGE_RESPONSE_WITH_DIMS);
    const judge = new LLMJudge({
      provider,
      model: "test",
      rubric: "Be creative",
    });
    // Access the private method for testing
    const prompt = (judge as any).buildJudgePrompt({
      taskPrompt: "task",
      agentOutput: "output",
      pinnedDimensions: ["creativity", "depth"],
    });
    expect(prompt).toContain("## Required Dimensions");
    expect(prompt).toContain("creativity");
    expect(prompt).toContain("depth");
    expect(prompt).toContain("Do not add, remove, or rename dimensions");
  });

  it("omits required dimensions section when not pinned", async () => {
    const provider = makeMockProvider(JUDGE_RESPONSE_WITH_DIMS);
    const judge = new LLMJudge({
      provider,
      model: "test",
      rubric: "Be creative",
    });
    const prompt = (judge as any).buildJudgePrompt({
      taskPrompt: "task",
      agentOutput: "output",
    });
    expect(prompt).not.toContain("## Required Dimensions");
  });

  it("passes pinned dimensions through evaluate()", async () => {
    const capturedPrompts: string[] = [];
    const provider: LLMProvider = {
      name: "capture-mock",
      defaultModel: () => "m",
      complete: async (opts) => {
        capturedPrompts.push(opts.userPrompt);
        return { text: JUDGE_RESPONSE_WITH_DIMS, usage: {} };
      },
    };
    const judge = new LLMJudge({
      provider,
      model: "test",
      rubric: "Be creative",
    });
    await judge.evaluate({
      taskPrompt: "task",
      agentOutput: "output",
      pinnedDimensions: ["creativity", "depth"],
    });
    expect(capturedPrompts.length).toBe(1);
    expect(capturedPrompts[0]).toContain("## Required Dimensions");
  });
});

describe("Improvement loop dimension pinning", () => {
  it("pins dimensions after first successful round", async () => {
    const capturedPinned: Array<string[] | undefined> = [];
    const scores = [0.6, 0.75, 0.95];
    let callCount = 0;

    const task: AgentTaskInterface = {
      getTaskPrompt: () => "test",
      getRubric: () => "test rubric",
      initialState: () => ({}),
      describeTask: () => "test",
      evaluateOutput: async (_output, _state, opts) => {
        capturedPinned.push(opts?.pinnedDimensions);
        const idx = Math.min(callCount, scores.length - 1);
        const score = scores[idx];
        callCount++;
        return {
          score,
          reasoning: `Score ${score}`,
          dimensionScores: { creativity: score, depth: score * 0.8 },
        };
      },
      reviseOutput: async (out) => out + " [revised]",
    };

    const loop = new ImprovementLoop({
      task,
      maxRounds: 3,
      qualityThreshold: 0.99,
    });
    await loop.run({ initialOutput: "initial output", state: {} });

    // First call: no pinning yet
    expect(capturedPinned[0]).toBeUndefined();
    // Subsequent calls: should have pinned dimensions
    for (const pinned of capturedPinned.slice(1)) {
      expect(pinned).toBeDefined();
      expect([...(pinned ?? [])].sort()).toEqual(["creativity", "depth"]);
    }
  });

  it("does not pin when no dimension scores", async () => {
    const capturedPinned: Array<string[] | undefined> = [];
    let callCount = 0;

    const task: AgentTaskInterface = {
      getTaskPrompt: () => "test",
      getRubric: () => "test rubric",
      initialState: () => ({}),
      describeTask: () => "test",
      evaluateOutput: async (_output, _state, opts) => {
        capturedPinned.push(opts?.pinnedDimensions);
        callCount++;
        return {
          score: 0.5,
          reasoning: "ok",
          dimensionScores: {},
        };
      },
      reviseOutput: async (out) => out + " [revised]",
    };

    const loop = new ImprovementLoop({
      task,
      maxRounds: 3,
      qualityThreshold: 0.99,
    });
    await loop.run({ initialOutput: "initial", state: {} });

    // All calls should have undefined pinned
    expect(capturedPinned.every((p) => p === undefined)).toBe(true);
  });
});

describe("No pinning when dimensions explicit", () => {
  it("dimensionsWereGenerated is false when rubric mentions dimensions", async () => {
    const resp =
      '<!-- JUDGE_RESULT_START -->' +
      '{"score": 0.8, "reasoning": "ok", ' +
      '"dimensions": {"clarity": 0.9, "accuracy": 0.7}}' +
      '<!-- JUDGE_RESULT_END -->';
    const provider = makeMockProvider(resp);
    const judge = new LLMJudge({
      provider,
      model: "test",
      rubric: "Evaluate clarity and accuracy of the output",
    });
    const result = await judge.evaluate({
      taskPrompt: "task",
      agentOutput: "output",
    });
    expect(result.dimensionsWereGenerated).toBe(false);
  });
});

describe("SimpleAgentTask pinned dimensions", () => {
  it("passes pinned dimensions through to judge", async () => {
    const provider = makeMockProvider(JUDGE_RESPONSE_WITH_DIMS);
    const task = new SimpleAgentTask(
      "Do task",
      "Be creative",
      provider,
      "test",
    );
    const result = await task.evaluateOutput("test output", {}, {
      pinnedDimensions: ["creativity", "depth"],
    });
    expect(result.score).toBeGreaterThan(0);
    expect(result.dimensionScores.creativity).toBeDefined();
  });
});
