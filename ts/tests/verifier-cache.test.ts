/**
 * AC-902: content-fingerprint verdict cache + improvement-loop integration.
 * Mirrors Python's tests/test_verifier_cache.py and
 * tests/test_improvement_loop_caching.py.
 */

import { describe, it, expect } from "vitest";
import { EvaluationCache, contentFingerprint } from "../src/execution/verifier-cache.js";
import { ImprovementLoop } from "../src/execution/improvement-loop.js";
import type { AgentTaskInterface, AgentTaskResult } from "../src/types/index.js";

function verdict(score: number, passed: boolean) {
  return { score, reasoning: "r", dimensionScores: {}, passed };
}

describe("contentFingerprint", () => {
  it("is stable, content-sensitive, and salt-sensitive", () => {
    expect(contentFingerprint("theorem foo")).toBe(contentFingerprint("theorem foo"));
    expect(contentFingerprint("theorem foo")).not.toBe(contentFingerprint("theorem bar"));
    expect(contentFingerprint("x")).not.toBe(contentFingerprint("x", { salt: "mathlib-4.9" }));
  });
});

describe("EvaluationCache", () => {
  it("round-trips with stats", () => {
    const cache = new EvaluationCache();
    const fp = contentFingerprint("artifact");
    expect(cache.get(fp)).toBeUndefined();
    cache.put(fp, verdict(0.4, false));
    expect(cache.get(fp)?.score).toBe(0.4);
    expect(cache.stats()).toEqual({ hits: 1, misses: 1, entries: 1 });
  });

  it("unchangedFailure only for failed verdicts", () => {
    const cache = new EvaluationCache();
    cache.put(contentFingerprint("bad"), verdict(0.2, false));
    cache.put(contentFingerprint("good"), verdict(0.95, true));
    expect(cache.unchangedFailure(contentFingerprint("bad"))).toBe(true);
    expect(cache.unchangedFailure(contentFingerprint("good"))).toBe(false);
    expect(cache.unchangedFailure(contentFingerprint("unseen"))).toBe(false);
  });
});

function makeTask(opts: {
  score: (output: string) => number;
  revise: (output: string) => string;
}): AgentTaskInterface & { evaluateCalls: string[] } {
  const calls: string[] = [];
  return {
    evaluateCalls: calls,
    getTaskPrompt: () => "task",
    describeTask: () => "test task",
    getRubric: () => "rubric",
    initialState: () => ({}),
    evaluateOutput: async (output: string): Promise<AgentTaskResult> => {
      calls.push(output);
      return {
        score: opts.score(output),
        reasoning: "needs work",
        dimensionScores: {},
        internalRetries: 0,
        evaluatorEpoch: null,
      };
    },
    reviseOutput: async (output: string) => opts.revise(output),
  } as unknown as AgentTaskInterface & { evaluateCalls: string[] };
}

describe("improvement loop caching (AC-902)", () => {
  it("score-diverse oscillation stops via the cache backstop", async () => {
    const task = makeTask({
      score: (output) => (output === "output A" ? 0.3 : 0.6),
      revise: (output) => (output === "output A" ? "output B" : "output A"),
    });
    const loop = new ImprovementLoop({ task, maxRounds: 8, qualityThreshold: 0.9 });
    const result = await loop.run({ initialOutput: "output A", state: {} });
    expect(task.evaluateCalls.length).toBe(2);
    expect(result.terminationReason).toBe("unchanged_output");
    expect(result.totalRounds).toBe(4);
    expect(result.verifierCache?.entries).toBe(2);
    expect(result.verifierCache?.hits).toBeGreaterThanOrEqual(1);
  });

  it("changed artifacts always re-evaluate", async () => {
    const task = makeTask({
      score: () => 0.95,
      revise: (output) => output + " more",
    });
    const loop = new ImprovementLoop({ task, maxRounds: 3, qualityThreshold: 0.99, minRounds: 3 });
    await loop.run({ initialOutput: "seed", state: {} });
    expect(task.evaluateCalls.length).toBe(3);
  });

  it("missing required target fails closed without the judge", async () => {
    const task = makeTask({
      score: () => 0.95,
      revise: (output) => output + " theorem foo := rfl",
    });
    const loop = new ImprovementLoop({
      task,
      maxRounds: 2,
      qualityThreshold: 0.9,
      requiredTargets: ["theorem foo"],
    });
    const result = await loop.run({ initialOutput: "empty artifact", state: {} });
    expect(result.rounds[0].score).toBe(0);
    expect(result.rounds[0].reasoning).toContain("theorem foo");
    expect(task.evaluateCalls.length).toBe(1);
    expect(result.bestScore).toBe(0.95);
  });
});

describe("review regressions (AC-902)", () => {
  it("targets-missing round cannot become best under an epoch-carrying judge", async () => {
    const calls: string[] = [];
    const task = {
      getTaskPrompt: () => "task",
      describeTask: () => "t",
      getRubric: () => "rubric",
      initialState: () => ({}),
      evaluateOutput: async (output: string) => {
        calls.push(output);
        return {
          score: 0.85,
          reasoning: "good",
          dimensionScores: {},
          internalRetries: 0,
          evaluatorEpoch: "epoch-E",
        };
      },
      reviseOutput: async () => "artifact without the target",
    } as unknown as AgentTaskInterface;
    const loop = new ImprovementLoop({
      task,
      maxRounds: 2,
      qualityThreshold: 0.95,
      requiredTargets: ["theorem foo"],
    });
    const result = await loop.run({ initialOutput: "theorem foo := by simp", state: {} });
    expect(result.bestScore).toBe(0.85);
    expect(result.bestOutput).toBe("theorem foo := by simp");
  });
});

describe("cached score is artifact-intrinsic (AC-902 review fix)", () => {
  // Fact-check penalty and veto zeroing depend only on the artifact and are
  // embedded in the cached verdict; the delta-cap clamp depends on the
  // previous round's baseline (evaluation context) and must be re-derived at
  // replay time, never frozen into the cache.

  function makeFactPenalizedTask(): ReturnType<typeof makeTask> {
    const task = makeTask({
      score: () => 0.72,
      revise: (output) => (output === "output A" ? "output B" : "output A"),
    });
    (task as unknown as { verifyFacts: unknown }).verifyFacts = async () => ({
      verified: false,
      issues: ["claim X unsupported"],
    });
    return task;
  }

  it("fact-check penalty survives cache replay", async () => {
    const task = makeFactPenalizedTask();
    const loop = new ImprovementLoop({ task, maxRounds: 6, qualityThreshold: 0.7 });
    const result = await loop.run({ initialOutput: "output A", state: {} });
    expect(task.evaluateCalls.length).toBe(2);
    // a cached replay must not resurrect the unpenalized 0.72 judge score
    // (which would clear the 0.7 gate the artifact honestly failed)
    for (const round of result.rounds) {
      expect(round.score).toBeCloseTo(0.648, 9);
    }
    expect(result.bestScore).toBeCloseTo(0.648, 9);
  });

  it("cap clamp is not frozen into the cache", async () => {
    const task = makeTask({
      score: (output) => (output === "output A" ? 0.3 : 0.9),
      revise: (output) => (output === "output A" ? "output B" : "output A"),
    });
    const loop = new ImprovementLoop({
      task,
      maxRounds: 4,
      qualityThreshold: 0.95,
      capScoreJumps: true,
      maxScoreDelta: 0.2,
    });
    const result = await loop.run({ initialOutput: "output A", state: {} });
    expect(task.evaluateCalls.length).toBe(2);
    // round 2 judged B at 0.9 (effective capped to 0.5 against the 0.3
    // baseline); round 4 replays B and must carry the intrinsic 0.9, not the
    // frozen clamp from round 2's context
    expect(result.rounds[3].score).toBeCloseTo(0.9, 9);
  });
});
