import { describe, it, expect } from "vitest";
import {
  ImprovementLoop,
  isParseFailure,
  isImproved,
} from "../src/execution/improvement-loop.js";
import type { AgentTaskInterface, AgentTaskResult, RoundResult } from "../src/types/index.js";

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

describe("isParseFailure", () => {
  it("returns false for real zero", () => {
    expect(isParseFailure(0, "Terrible output")).toBe(false);
  });
  it("returns false for nonzero", () => {
    expect(isParseFailure(0.5, "no parseable score found")).toBe(false);
  });
  it("detects parse failure", () => {
    expect(
      isParseFailure(0, "Failed to parse judge response: no parseable score found"),
    ).toBe(true);
  });
});

describe("isImproved", () => {
  it("needs 2+ valid rounds", () => {
    expect(isImproved([])).toBe(false);
    expect(
      isImproved([
        { roundNumber: 1, output: "", score: 0.5, reasoning: "", dimensionScores: {}, isRevision: false, judgeFailed: false },
      ]),
    ).toBe(false);
  });
  it("ignores failed rounds", () => {
    const rounds: RoundResult[] = [
      { roundNumber: 1, output: "", score: 0.5, reasoning: "", dimensionScores: {}, isRevision: false, judgeFailed: false },
      { roundNumber: 2, output: "", score: 0, reasoning: "", dimensionScores: {}, isRevision: true, judgeFailed: true },
      { roundNumber: 3, output: "", score: 0.7, reasoning: "", dimensionScores: {}, isRevision: true, judgeFailed: false },
    ];
    expect(isImproved(rounds)).toBe(true);
  });
});

describe("ImprovementLoop", () => {
  it("meets threshold on first round", async () => {
    const task = makeFakeTask([{ score: 0.95, reasoning: "great", dimensionScores: {} }]);
    const loop = new ImprovementLoop({ task, maxRounds: 3, qualityThreshold: 0.9 });
    const result = await loop.run({ initialOutput: "test", state: {} });
    expect(result.metThreshold).toBe(true);
    expect(result.bestScore).toBe(0.95);
    expect(result.totalRounds).toBe(1);
  });

  it("improves over multiple rounds", async () => {
    const task = makeFakeTask([
      { score: 0.5, reasoning: "ok", dimensionScores: {} },
      { score: 0.95, reasoning: "great", dimensionScores: {} },
    ]);
    const loop = new ImprovementLoop({ task, maxRounds: 3, qualityThreshold: 0.9 });
    const result = await loop.run({ initialOutput: "test", state: {} });
    expect(result.metThreshold).toBe(true);
    expect(result.bestScore).toBe(0.95);
    expect(result.totalRounds).toBe(2);
  });

  it("stops when output unchanged", async () => {
    const task = makeFakeTask(
      [{ score: 0.5, reasoning: "ok", dimensionScores: {} }],
      (out) => out, // Return unchanged
    );
    const loop = new ImprovementLoop({ task, maxRounds: 5, qualityThreshold: 0.9 });
    const result = await loop.run({ initialOutput: "test", state: {} });
    expect(result.metThreshold).toBe(false);
    expect(result.totalRounds).toBe(1);
  });

  it("handles judge parse failure gracefully", async () => {
    const task = makeFakeTask([
      { score: 0, reasoning: "Failed to parse judge response: no parseable score found", dimensionScores: {} },
      { score: 0.8, reasoning: "good", dimensionScores: {} },
    ]);
    const loop = new ImprovementLoop({ task, maxRounds: 3, qualityThreshold: 0.9 });
    const result = await loop.run({ initialOutput: "test", state: {} });
    expect(result.judgeFailures).toBe(1);
    expect(result.bestScore).toBe(0.8);
  });

  it("aborts after 3 consecutive failures", async () => {
    const task = makeFakeTask([
      { score: 0, reasoning: "Failed to parse judge response: no parseable score found", dimensionScores: {} },
    ]);
    const loop = new ImprovementLoop({ task, maxRounds: 10, qualityThreshold: 0.9 });
    const result = await loop.run({ initialOutput: "test", state: {} });
    expect(result.judgeFailures).toBe(3);
    expect(result.totalRounds).toBe(3);
  });

  it("carries forward last good feedback on failure", async () => {
    const revisions: string[] = [];
    const task = makeFakeTask(
      [
        { score: 0.6, reasoning: "Needs detail", dimensionScores: {} },
        { score: 0, reasoning: "Failed to parse judge response: no parseable score found", dimensionScores: {} },
        { score: 0.85, reasoning: "Better", dimensionScores: {} },
      ],
      (out, res) => {
        revisions.push(res.reasoning);
        return `${out} [revised]`;
      },
    );
    const loop = new ImprovementLoop({ task, maxRounds: 4, qualityThreshold: 0.9 });
    const result = await loop.run({ initialOutput: "test", state: {} });
    expect(result.judgeFailures).toBe(1);
    // Second revision should use "Needs detail" (carried forward)
    expect(revisions[1]).toBe("Needs detail");
  });
});
