import { describe, it, expect, vi } from "vitest";
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
    expect(result.terminationReason).toBe("threshold_met");
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
    expect(result.terminationReason).toBe("threshold_met");
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
    expect(result.terminationReason).toBe("unchanged_output");
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
    expect(result.terminationReason).toBe("consecutive_failures");
  });

  it("calls verifyFacts and appends issues to reasoning", async () => {
    let verifyCalled = false;
    const task: AgentTaskInterface = {
      getTaskPrompt: () => "test",
      getRubric: () => "test rubric",
      initialState: () => ({}),
      describeTask: () => "test task",
      evaluateOutput: async () => ({
        score: 0.95,
        reasoning: "good",
        dimensionScores: {},
      }),
      reviseOutput: async (out) => `${out} [revised]`,
      verifyFacts: async () => {
        verifyCalled = true;
        return { verified: false, issues: ["Date is wrong", "Name misspelled"] };
      },
    };
    const loop = new ImprovementLoop({ task, maxRounds: 1, qualityThreshold: 0.9 });
    const result = await loop.run({ initialOutput: "test", state: {} });
    expect(verifyCalled).toBe(true);
    expect(result.rounds[0].reasoning).toContain("Fact-check issues");
    expect(result.rounds[0].reasoning).toContain("Date is wrong");
    expect(result.rounds[0].reasoning).toContain("Name misspelled");
    // Score is penalized by 0.9x when facts are unverified
    expect(result.bestScore).toBe(0.95 * 0.9);
  });

  it("threshold sensitivity: score 0.91 with threshold 0.90 does not stop immediately", async () => {
    let evalCount = 0;
    const task: AgentTaskInterface = {
      getTaskPrompt: () => "test",
      getRubric: () => "test rubric",
      initialState: () => ({}),
      describeTask: () => "test task",
      evaluateOutput: async () => {
        evalCount++;
        // Round 1: 0.91 (within 0.02 of 0.90 threshold)
        // Round 2: 0.91 (confirm stable)
        return { score: 0.91, reasoning: `round ${evalCount}`, dimensionScores: {} };
      },
      reviseOutput: async (out) => `${out} [revised]`,
    };
    const loop = new ImprovementLoop({ task, maxRounds: 5, qualityThreshold: 0.90 });
    const result = await loop.run({ initialOutput: "test", state: {} });
    expect(result.metThreshold).toBe(true);
    // Should run at least 2 rounds since 0.91 is within 0.02 of 0.90
    expect(result.totalRounds).toBe(2);
    expect(evalCount).toBe(2);
  });

  it("threshold sensitivity: score clearly above threshold stops immediately", async () => {
    let evalCount = 0;
    const task: AgentTaskInterface = {
      getTaskPrompt: () => "test",
      getRubric: () => "test rubric",
      initialState: () => ({}),
      describeTask: () => "test task",
      evaluateOutput: async () => {
        evalCount++;
        return { score: 0.95, reasoning: "great", dimensionScores: {} };
      },
      reviseOutput: async (out) => `${out} [revised]`,
    };
    const loop = new ImprovementLoop({ task, maxRounds: 5, qualityThreshold: 0.90 });
    const result = await loop.run({ initialOutput: "test", state: {} });
    expect(result.metThreshold).toBe(true);
    expect(result.totalRounds).toBe(1);
    expect(evalCount).toBe(1);
  });

  it("threshold sensitivity: score drops below threshold after near-miss continues", async () => {
    let evalCount = 0;
    const task: AgentTaskInterface = {
      getTaskPrompt: () => "test",
      getRubric: () => "test rubric",
      initialState: () => ({}),
      describeTask: () => "test task",
      evaluateOutput: async () => {
        evalCount++;
        // Round 1: 0.91 (near threshold), Round 2: 0.85 (drops below)
        // Round 3: 0.91 (near again), Round 4: 0.91 (confirmed)
        const scores = [0.91, 0.85, 0.91, 0.91];
        const score = scores[Math.min(evalCount - 1, scores.length - 1)];
        return { score, reasoning: `round ${evalCount}`, dimensionScores: {} };
      },
      reviseOutput: async (out) => `${out} [revised]`,
    };
    const loop = new ImprovementLoop({ task, maxRounds: 5, qualityThreshold: 0.90 });
    const result = await loop.run({ initialOutput: "test", state: {} });
    expect(result.metThreshold).toBe(true);
    // Should have run 4 rounds: near-miss, drop, near-miss, confirmed
    expect(result.totalRounds).toBe(4);
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

  it("sets terminationReason to max_rounds when exhausted", async () => {
    const task = makeFakeTask([
      { score: 0.3, reasoning: "low", dimensionScores: {} },
      { score: 0.5, reasoning: "mid", dimensionScores: {} },
      { score: 0.6, reasoning: "better", dimensionScores: {} },
    ]);
    const loop = new ImprovementLoop({ task, maxRounds: 3, qualityThreshold: 0.9 });
    const result = await loop.run({ initialOutput: "test", state: {} });
    expect(result.metThreshold).toBe(false);
    expect(result.terminationReason).toBe("max_rounds");
  });
});

describe("Plateau detection", () => {
  it("detects plateau after 2 consecutive near-identical scores", async () => {
    const task = makeFakeTask([
      { score: 0.5, reasoning: "ok", dimensionScores: {} },
      { score: 0.505, reasoning: "ok", dimensionScores: {} },
      { score: 0.508, reasoning: "ok", dimensionScores: {} },
    ]);
    const loop = new ImprovementLoop({ task, maxRounds: 10, qualityThreshold: 0.9 });
    const result = await loop.run({ initialOutput: "test", state: {} });
    expect(result.terminationReason).toBe("plateau_stall");
    // Should stop at round 3 (2 consecutive plateaus: round1->2, round2->3)
    expect(result.totalRounds).toBe(3);
  });

  it("resets plateau counter on significant score change", async () => {
    const task = makeFakeTask([
      { score: 0.5, reasoning: "ok", dimensionScores: {} },
      { score: 0.505, reasoning: "ok", dimensionScores: {} },  // plateau +1
      { score: 0.7, reasoning: "jump", dimensionScores: {} },  // reset
      { score: 0.705, reasoning: "ok", dimensionScores: {} },  // plateau +1
      { score: 0.95, reasoning: "great", dimensionScores: {} },
    ]);
    const loop = new ImprovementLoop({ task, maxRounds: 10, qualityThreshold: 0.9 });
    const result = await loop.run({ initialOutput: "test", state: {} });
    expect(result.terminationReason).toBe("threshold_met");
    expect(result.totalRounds).toBe(5);
  });

  it("does not detect plateau with only 1 near-identical score", async () => {
    const task = makeFakeTask([
      { score: 0.5, reasoning: "ok", dimensionScores: {} },
      { score: 0.505, reasoning: "ok", dimensionScores: {} },  // plateau +1
      { score: 0.7, reasoning: "jump", dimensionScores: {} },  // reset
      { score: 0.95, reasoning: "great", dimensionScores: {} },
    ]);
    const loop = new ImprovementLoop({ task, maxRounds: 10, qualityThreshold: 0.9 });
    const result = await loop.run({ initialOutput: "test", state: {} });
    expect(result.terminationReason).toBe("threshold_met");
  });
});

describe("Dimension trajectory", () => {
  it("builds trajectory from valid rounds", async () => {
    const task = makeFakeTask([
      { score: 0.5, reasoning: "ok", dimensionScores: { clarity: 0.4, accuracy: 0.6 } },
      { score: 0.7, reasoning: "better", dimensionScores: { clarity: 0.6, accuracy: 0.8 } },
      { score: 0.95, reasoning: "great", dimensionScores: { clarity: 0.9, accuracy: 1.0 } },
    ]);
    const loop = new ImprovementLoop({ task, maxRounds: 5, qualityThreshold: 0.9 });
    const result = await loop.run({ initialOutput: "test", state: {} });
    expect(result.dimensionTrajectory).toEqual({
      clarity: [0.4, 0.6, 0.9],
      accuracy: [0.6, 0.8, 1.0],
    });
  });

  it("skips failed rounds in trajectory", async () => {
    const task = makeFakeTask([
      { score: 0.5, reasoning: "ok", dimensionScores: { quality: 0.5 } },
      { score: 0, reasoning: "Failed to parse judge response: no parseable score found", dimensionScores: {} },
      { score: 0.95, reasoning: "great", dimensionScores: { quality: 0.9 } },
    ]);
    const loop = new ImprovementLoop({ task, maxRounds: 5, qualityThreshold: 0.9 });
    const result = await loop.run({ initialOutput: "test", state: {} });
    expect(result.dimensionTrajectory).toEqual({ quality: [0.5, 0.9] });
  });

  it("returns empty trajectory when no dimension scores", async () => {
    const task = makeFakeTask([{ score: 0.95, reasoning: "great", dimensionScores: {} }]);
    const loop = new ImprovementLoop({ task, maxRounds: 3, qualityThreshold: 0.9 });
    const result = await loop.run({ initialOutput: "test", state: {} });
    expect(result.dimensionTrajectory).toEqual({});
  });
});

describe("Minimum revision rounds", () => {
  it("continues past threshold when minRounds not yet reached", async () => {
    const task = makeFakeTask([
      { score: 0.95, reasoning: "great", dimensionScores: {} },
      { score: 0.96, reasoning: "even better", dimensionScores: {} },
      { score: 0.97, reasoning: "best", dimensionScores: {} },
    ]);
    const loop = new ImprovementLoop({ task, maxRounds: 5, qualityThreshold: 0.9, minRounds: 3 });
    const result = await loop.run({ initialOutput: "test", state: {} });
    expect(result.metThreshold).toBe(true);
    expect(result.terminationReason).toBe("threshold_met");
    expect(result.totalRounds).toBe(3);
    expect(result.bestScore).toBe(0.97);
  });

  it("stops at threshold when minRounds already met", async () => {
    const task = makeFakeTask([
      { score: 0.5, reasoning: "ok", dimensionScores: {} },
      { score: 0.95, reasoning: "great", dimensionScores: {} },
    ]);
    const loop = new ImprovementLoop({ task, maxRounds: 5, qualityThreshold: 0.9, minRounds: 1 });
    const result = await loop.run({ initialOutput: "test", state: {} });
    expect(result.metThreshold).toBe(true);
    expect(result.totalRounds).toBe(2);
  });

  it("defaults minRounds to 1", async () => {
    const task = makeFakeTask([{ score: 0.95, reasoning: "great", dimensionScores: {} }]);
    const loop = new ImprovementLoop({ task, maxRounds: 5, qualityThreshold: 0.9 });
    const result = await loop.run({ initialOutput: "test", state: {} });
    expect(result.totalRounds).toBe(1);
  });
});

describe("Max score delta", () => {
  it("warns on large score jump", async () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    const task = makeFakeTask([
      { score: 0.2, reasoning: "low", dimensionScores: {} },
      { score: 0.95, reasoning: "great", dimensionScores: {} },
    ]);
    const loop = new ImprovementLoop({ task, maxRounds: 3, qualityThreshold: 0.9, maxScoreDelta: 0.5 });
    await loop.run({ initialOutput: "test", state: {} });
    expect(warnSpy).toHaveBeenCalledOnce();
    expect(warnSpy.mock.calls[0][0]).toContain("Score jump");
    warnSpy.mockRestore();
  });

  it("does not warn when delta within limit", async () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    const task = makeFakeTask([
      { score: 0.5, reasoning: "ok", dimensionScores: {} },
      { score: 0.95, reasoning: "great", dimensionScores: {} },
    ]);
    const loop = new ImprovementLoop({ task, maxRounds: 3, qualityThreshold: 0.9, maxScoreDelta: 0.5 });
    await loop.run({ initialOutput: "test", state: {} });
    expect(warnSpy).not.toHaveBeenCalled();
    warnSpy.mockRestore();
  });

  it("caps score when capScoreJumps is true", async () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    const task = makeFakeTask([
      { score: 0.2, reasoning: "low", dimensionScores: {} },
      { score: 0.9, reasoning: "huge jump", dimensionScores: {} },
      { score: 0.95, reasoning: "great", dimensionScores: {} },
    ]);
    const loop = new ImprovementLoop({
      task, maxRounds: 5, qualityThreshold: 0.99,
      maxScoreDelta: 0.3, capScoreJumps: true,
    });
    const result = await loop.run({ initialOutput: "test", state: {} });
    // Round 2: 0.2 -> 0.9, capped to 0.2 + 0.3 = 0.5
    // bestScore should be capped at 0.5 (from round 2), then round 3 score 0.95
    // but round 3 compares against prevValidScore=0.9 (raw), delta=0.05 < 0.3, no cap
    // So bestScore should be 0.95 from round 3
    expect(result.bestScore).toBe(0.95);
    expect(warnSpy).toHaveBeenCalled();
    warnSpy.mockRestore();
  });

  it("does not cap score when capScoreJumps is false (default)", async () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    const task = makeFakeTask([
      { score: 0.2, reasoning: "low", dimensionScores: {} },
      { score: 0.95, reasoning: "great", dimensionScores: {} },
    ]);
    const loop = new ImprovementLoop({ task, maxRounds: 3, qualityThreshold: 0.9, maxScoreDelta: 0.3 });
    const result = await loop.run({ initialOutput: "test", state: {} });
    // Score should NOT be capped, even though delta > 0.3
    expect(result.bestScore).toBe(0.95);
    warnSpy.mockRestore();
  });
});
