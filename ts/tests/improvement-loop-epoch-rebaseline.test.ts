import { describe, it, expect, vi } from "vitest";
import { ImprovementLoop } from "../src/execution/improvement-loop.js";
import type { AgentTaskInterface, AgentTaskResult } from "../src/types/index.js";

/**
 * Round 1 scored under epoch e1 (score 0.9), round 2 under epoch e2 (score 0.4).
 * Mirrors the Python _EpochSwapTask in test_improvement_loop_epoch_rebaseline.py.
 */
function makeEpochSwapTask(): AgentTaskInterface {
  let n = 0;
  return {
    getTaskPrompt: () => "do it",
    getRubric: () => "rubric",
    initialState: () => ({}),
    describeTask: () => "t",
    evaluateOutput: async (): Promise<AgentTaskResult> => {
      n += 1;
      if (n === 1) {
        return {
          score: 0.9,
          reasoning: "e1",
          dimensionScores: {},
          internalRetries: 0,
          evaluatorEpoch: "e1",
        };
      }
      return {
        score: 0.4,
        reasoning: "e2",
        dimensionScores: {},
        internalRetries: 0,
        evaluatorEpoch: "e2",
      };
    },
    reviseOutput: async (out) => `${out} revised`,
  };
}

/**
 * Every round barely meets the 0.9 threshold (0.91), but round 1 is scored under
 * epoch e1 and rounds 2+ under epoch e2. Reproduces the reviewer's case: a
 * prior-epoch threshold-met round must not confirm a new-epoch round as stable.
 */
function makeThresholdEpochSwapTask(): AgentTaskInterface {
  let n = 0;
  return {
    getTaskPrompt: () => "do it",
    getRubric: () => "rubric",
    initialState: () => ({}),
    describeTask: () => "t",
    evaluateOutput: async (): Promise<AgentTaskResult> => {
      n += 1;
      const epoch = n === 1 ? "e1" : "e2";
      return {
        score: 0.91,
        reasoning: epoch,
        dimensionScores: {},
        internalRetries: 0,
        evaluatorEpoch: epoch,
      };
    },
    reviseOutput: async (out) => `${out} r`,
  };
}

describe("ImprovementLoop evaluator-epoch re-baseline (AC-885)", () => {
  it("resets the near-threshold stability tracker on rebaseline so a prior-epoch threshold-met cannot confirm a new-epoch round", async () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    try {
      const loop = new ImprovementLoop({
        task: makeThresholdEpochSwapTask(),
        maxRounds: 3,
        qualityThreshold: 0.9,
        minRounds: 1,
        maxScoreDelta: 1.0,
      });
      const result = await loop.run({ initialOutput: "seed output", state: {} });

      // WITHOUT the fix, round 1's e1 threshold-met confirms round 2's e2 round as
      // "confirmed stable" and the loop stops at round 2. WITH the fix, round 2 is the
      // first threshold-met of epoch e2, so the loop must continue past round 2.
      expect(result.totalRounds).toBe(3);
      expect(result.rounds.map((r) => r.evaluatorEpoch)).toEqual(["e1", "e2", "e2"]);
    } finally {
      warnSpy.mockRestore();
    }
  });

  it("re-baselines on an epoch change, excludes the stale best, and fires no cross-epoch delta warning", async () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    try {
      const loop = new ImprovementLoop({
        task: makeEpochSwapTask(),
        maxRounds: 2,
        qualityThreshold: 2.0, // unreachable, force both rounds
        minRounds: 2,
        maxScoreDelta: 0.1, // a 0.9->0.4 cross-epoch drop would trip this if NOT re-baselined
      });
      const result = await loop.run({ initialOutput: "seed output", state: {} });

      // No cross-epoch max-score-delta warning fired.
      const deltaWarnings = warnSpy.mock.calls
        .map((c) => String(c[0]))
        .filter((m) => m.includes("Score jump of"));
      expect(deltaWarnings).toEqual([]);

      // After re-baseline, best reflects the new epoch (0.4), not the stale 0.9.
      expect(result.bestScore).toBe(0.4);
      expect(result.evaluatorEpoch).toBe("e2");

      // Round records carry their epochs.
      expect(result.rounds.map((r) => r.evaluatorEpoch)).toEqual(["e1", "e2"]);
    } finally {
      warnSpy.mockRestore();
    }
  });
});
