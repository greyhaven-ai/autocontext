import { describe, it, expect } from "vitest";
import { ImprovementLoop } from "../src/execution/improvement-loop.js";
import type { AgentTaskInterface, AgentTaskResult } from "../src/types/index.js";

function makeFakeTask(scores: number[]): AgentTaskInterface {
  let callCount = 0;
  return {
    getTaskPrompt: () => "test",
    getRubric: () => "test rubric",
    initialState: () => ({}),
    describeTask: () => "test task",
    evaluateOutput: async () => {
      const idx = Math.min(callCount, scores.length - 1);
      callCount++;
      return {
        score: scores[idx],
        reasoning: "ok",
        dimensionScores: {},
        internalRetries: 0,
      };
    },
    reviseOutput: async (out) => `${out} [revised]`,
  };
}

describe("Per-task metrics tracking", () => {
  it("result has durationMs as a non-negative number", async () => {
    const task = makeFakeTask([0.5]);
    const loop = new ImprovementLoop({ task, maxRounds: 1, qualityThreshold: 0.9 });
    const result = await loop.run({ initialOutput: "hello", state: {} });
    expect(result.durationMs).toBeDefined();
    expect(typeof result.durationMs).toBe("number");
    expect(result.durationMs!).toBeGreaterThanOrEqual(0);
  });

  it("result has apiCalls equal to number of rounds", async () => {
    const task = makeFakeTask([0.4, 0.5, 0.95]);
    const loop = new ImprovementLoop({ task, maxRounds: 3, qualityThreshold: 0.9 });
    const result = await loop.run({ initialOutput: "hello", state: {} });
    expect(result.apiCalls).toBe(result.totalRounds);
  });

  it("each round has roundDurationMs as a non-negative number", async () => {
    const task = makeFakeTask([0.4, 0.95]);
    const loop = new ImprovementLoop({ task, maxRounds: 3, qualityThreshold: 0.9 });
    const result = await loop.run({ initialOutput: "hello", state: {} });
    expect(result.rounds.length).toBeGreaterThanOrEqual(1);
    for (const rr of result.rounds) {
      expect(rr.roundDurationMs).toBeDefined();
      expect(typeof rr.roundDurationMs).toBe("number");
      expect(rr.roundDurationMs!).toBeGreaterThanOrEqual(0);
    }
  });
});
