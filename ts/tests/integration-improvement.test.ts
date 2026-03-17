/**
 * Integration test: 3-round improvement cycle (AC-30).
 *
 * Validates improvement loop: agent revises based on feedback, score improves.
 * Uses mock task with improving scores across rounds.
 */

import { describe, it, expect } from "vitest";
import { ImprovementLoop, isImproved } from "../src/execution/improvement-loop.js";
import type { AgentTaskInterface, AgentTaskResult } from "../src/types/index.js";

const SCORES = [0.55, 0.72, 0.88];

function createImprovingTask(): AgentTaskInterface & { evalCount: number } {
  let evalCount = 0;
  let reviseCount = 0;

  return {
    evalCount: 0,
    getTaskPrompt: () => "Write a haiku about distributed systems",
    getRubric: () => "syllable accuracy (5-7-5), technical relevance, creativity",
    initialState: () => ({}),
    describeTask: () => "Write a haiku about distributed systems",

    evaluateOutput: async (output, _state) => {
      const score = SCORES[Math.min(evalCount, SCORES.length - 1)];
      evalCount++;
      // Update the external counter for assertions
      (task as { evalCount: number }).evalCount = evalCount;
      return {
        score,
        reasoning: `Round ${evalCount} feedback: score=${score.toFixed(2)}`,
        dimensionScores: {
          syllable_accuracy: Math.min(1.0, score + 0.05),
          technical_relevance: score,
          creativity: Math.max(0.0, score - 0.05),
        },
      };
    },

    reviseOutput: async (output, _judgeResult, _state) => {
      reviseCount++;
      return `Revised v${reviseCount}: improved content based on feedback`;
    },
  };

  // Create a reference so we can use it in the evaluateOutput closure
  var task = arguments[0]; // unused, just for closure
}

// Simpler approach: factory that returns task + counters
function makeImprovingTask() {
  let evalCount = 0;
  let reviseCount = 0;

  const task: AgentTaskInterface = {
    getTaskPrompt: () => "Write a haiku about distributed systems",
    getRubric: () => "syllable accuracy, technical relevance, creativity",
    initialState: () => ({}),
    describeTask: () => "Write a haiku about distributed systems",

    evaluateOutput: async () => {
      const score = SCORES[Math.min(evalCount, SCORES.length - 1)];
      evalCount++;
      return {
        score,
        reasoning: `Round ${evalCount} feedback: score=${score.toFixed(2)}`,
        dimensionScores: {
          syllable_accuracy: Math.min(1.0, score + 0.05),
          technical_relevance: score,
          creativity: Math.max(0.0, score - 0.05),
        },
      };
    },

    reviseOutput: async () => {
      reviseCount++;
      return `Revised v${reviseCount}: improved content`;
    },
  };

  return task;
}

describe("Integration: 3-round improvement cycle (AC-30)", () => {
  it("three rounds complete without error", async () => {
    const task = makeImprovingTask();
    const loop = new ImprovementLoop({ task, maxRounds: 3, qualityThreshold: 0.95 });
    const result = await loop.run({ initialOutput: "initial haiku", state: {} });
    expect(result.totalRounds).toBe(3);
    expect(result.rounds).toHaveLength(3);
  });

  it("score improves from round 1 to round 3", async () => {
    const task = makeImprovingTask();
    const loop = new ImprovementLoop({ task, maxRounds: 3, qualityThreshold: 0.95 });
    const result = await loop.run({ initialOutput: "initial haiku", state: {} });
    const validScores = result.rounds.filter((r) => !r.judgeFailed).map((r) => r.score);
    expect(validScores[validScores.length - 1]).toBeGreaterThan(validScores[0]);
  });

  it("final output is meaningfully better than initial (isImproved)", async () => {
    const task = makeImprovingTask();
    const loop = new ImprovementLoop({ task, maxRounds: 3, qualityThreshold: 0.95 });
    const result = await loop.run({ initialOutput: "initial haiku", state: {} });
    expect(isImproved(result.rounds)).toBe(true);
  });

  it("no parse failures across rounds", async () => {
    const task = makeImprovingTask();
    const loop = new ImprovementLoop({ task, maxRounds: 3, qualityThreshold: 0.95 });
    const result = await loop.run({ initialOutput: "initial haiku", state: {} });
    expect(result.judgeFailures).toBe(0);
  });

  it("round-by-round results saved for analysis", async () => {
    const task = makeImprovingTask();
    const loop = new ImprovementLoop({ task, maxRounds: 3, qualityThreshold: 0.95 });
    const result = await loop.run({ initialOutput: "initial haiku", state: {} });
    for (const r of result.rounds) {
      expect(r.score).toBeGreaterThan(0);
      expect(r.reasoning.length).toBeGreaterThan(0);
      expect(r.roundNumber).toBeGreaterThanOrEqual(1);
    }
  });

  it("dimension trajectory tracked across rounds", async () => {
    const task = makeImprovingTask();
    const loop = new ImprovementLoop({ task, maxRounds: 3, qualityThreshold: 0.95 });
    const result = await loop.run({ initialOutput: "initial haiku", state: {} });
    expect(result.dimensionTrajectory.syllable_accuracy).toHaveLength(3);
    expect(result.dimensionTrajectory.technical_relevance).toHaveLength(3);
    expect(result.dimensionTrajectory.creativity).toHaveLength(3);
  });

  it("best score is the highest across rounds", async () => {
    const task = makeImprovingTask();
    const loop = new ImprovementLoop({ task, maxRounds: 3, qualityThreshold: 0.95 });
    const result = await loop.run({ initialOutput: "initial haiku", state: {} });
    const maxScore = Math.max(...result.rounds.map((r) => r.score));
    expect(result.bestScore).toBe(maxScore);
    expect(result.bestRound).toBe(3);
  });
});
