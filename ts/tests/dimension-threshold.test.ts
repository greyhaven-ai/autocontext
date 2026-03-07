import { describe, it, expect } from "vitest";
import { ImprovementLoop } from "../src/execution/improvement-loop.js";
import type { AgentTaskInterface, AgentTaskResult } from "../src/types/index.js";

function makeFakeTask(
  results: AgentTaskResult[],
  revisionFn?: (out: string, res: AgentTaskResult) => string,
): AgentTaskInterface {
  let callCount = 0;
  return {
    getTaskPrompt: () => "test",
    getRubric: () => "test rubric",
    initialState: () => ({}),
    describeTask: () => "test task",
    evaluateOutput: async () => {
      const idx = Math.min(callCount, results.length - 1);
      callCount++;
      return results[idx];
    },
    reviseOutput: async (out, res) =>
      revisionFn ? revisionFn(out, res) : `${out} [revised]`,
  };
}

describe("Dimension threshold gating", () => {
  it("continues when overall meets threshold but a dimension fails", async () => {
    // R1: score=0.85, action=0.50 (below dim_threshold 0.8) -> continue
    // R2: score=0.87, action=0.78 (still below 0.8) -> continue
    // R3: score=0.90, action=0.85 (all dims >= 0.8) -> stop
    const task = makeFakeTask([
      { score: 0.85, reasoning: "round 1", dimensionScores: { clarity: 0.90, action: 0.50 }, internalRetries: 0 },
      { score: 0.87, reasoning: "round 2", dimensionScores: { clarity: 0.92, action: 0.78 }, internalRetries: 0 },
      { score: 0.90, reasoning: "round 3", dimensionScores: { clarity: 0.95, action: 0.85 }, internalRetries: 0 },
    ]);
    const loop = new ImprovementLoop({
      task, maxRounds: 5, qualityThreshold: 0.85,
      dimensionThreshold: 0.8,
    });
    const result = await loop.run({ initialOutput: "test", state: {} });
    // Should NOT stop at round 1 or 2 because action < 0.8
    expect(result.totalRounds).toBe(3);
    expect(result.metThreshold).toBe(true);
    expect(result.terminationReason).toBe("threshold_met");
  });

  it("stops immediately without dimension_threshold", async () => {
    const task = makeFakeTask([
      { score: 0.90, reasoning: "round 1", dimensionScores: { clarity: 0.95, action: 0.50 }, internalRetries: 0 },
      { score: 0.92, reasoning: "round 2", dimensionScores: { clarity: 0.97, action: 0.78 }, internalRetries: 0 },
    ]);
    const loop = new ImprovementLoop({
      task, maxRounds: 5, qualityThreshold: 0.85,
    });
    const result = await loop.run({ initialOutput: "test", state: {} });
    // 0.90 >= 0.85, clearly above -> stop at round 1
    expect(result.totalRounds).toBe(1);
    expect(result.metThreshold).toBe(true);
    expect(result.terminationReason).toBe("threshold_met");
  });

  it("continues past overall with dimension_threshold set", async () => {
    const task = makeFakeTask([
      { score: 0.90, reasoning: "round 1", dimensionScores: { clarity: 0.95, action: 0.50 }, internalRetries: 0 },
      { score: 0.92, reasoning: "round 2", dimensionScores: { clarity: 0.97, action: 0.85 }, internalRetries: 0 },
    ]);
    const loop = new ImprovementLoop({
      task, maxRounds: 5, qualityThreshold: 0.85,
      dimensionThreshold: 0.8,
    });
    const result = await loop.run({ initialOutput: "test", state: {} });
    // Round 1: overall 0.90 >= 0.85 BUT action 0.50 < 0.80 -> continue
    // Round 2: overall 0.92 >= 0.85 AND all dims >= 0.80 -> stop
    expect(result.totalRounds).toBe(2);
    expect(result.metThreshold).toBe(true);
    expect(result.terminationReason).toBe("threshold_met");
  });
});

describe("Worst dimension tracking", () => {
  it("tracks worst dimension in round results", async () => {
    const task = makeFakeTask([
      { score: 0.80, reasoning: "ok", dimensionScores: { clarity: 0.90, accuracy: 0.70, depth: 0.85 }, internalRetries: 0 },
      { score: 0.95, reasoning: "great", dimensionScores: { clarity: 0.95, accuracy: 0.90, depth: 0.92 }, internalRetries: 0 },
    ]);
    const loop = new ImprovementLoop({ task, maxRounds: 3, qualityThreshold: 0.9 });
    const result = await loop.run({ initialOutput: "test", state: {} });

    // Round 1: worst dimension is accuracy at 0.70
    expect(result.rounds[0].worstDimension).toBe("accuracy");
    expect(result.rounds[0].worstDimensionScore).toBe(0.70);

    // Round 2: worst dimension is accuracy at 0.90
    expect(result.rounds[1].worstDimension).toBe("accuracy");
    expect(result.rounds[1].worstDimensionScore).toBe(0.90);
  });

  it("leaves worst dimension undefined without dimension scores", async () => {
    const task = makeFakeTask([
      { score: 0.95, reasoning: "great", dimensionScores: {}, internalRetries: 0 },
    ]);
    const loop = new ImprovementLoop({ task, maxRounds: 1, qualityThreshold: 0.9 });
    const result = await loop.run({ initialOutput: "test", state: {} });
    expect(result.rounds[0].worstDimension).toBeUndefined();
    expect(result.rounds[0].worstDimensionScore).toBeUndefined();
  });
});
