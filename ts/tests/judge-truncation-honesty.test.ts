/**
 * AC-904: unparseable judge samples are excluded from the average, and
 * truncation (length stop) is named explicitly instead of scoring 0.
 * Mirrors Python's tests/test_judge.py TestTruncationHonesty.
 */

import { describe, it, expect } from "vitest";
import { LLMJudge } from "../src/judge/index.js";
import type { CompletionResult, LLMProvider } from "../src/types/index.js";

function scriptedProvider(
  responses: CompletionResult[],
  calls: Array<{ maxTokens?: number }>,
): LLMProvider {
  let index = 0;
  return {
    name: "scripted",
    defaultModel: () => "stub",
    complete: async (opts) => {
      calls.push({ maxTokens: opts.maxTokens });
      const response = responses[Math.min(index, responses.length - 1)];
      index += 1;
      return response;
    },
  };
}

function goodVerdict(score: number): CompletionResult {
  return { text: `{"score": ${score}, "reasoning": "ok", "dimensions": {}}`, usage: {} };
}

function garbage(stopReason?: string): CompletionResult {
  return { text: "mid-sentence trunca", usage: {}, stopReason };
}

describe("judge truncation honesty (AC-904)", () => {
  it("passes explicit maxTokens to the provider", async () => {
    const calls: Array<{ maxTokens?: number }> = [];
    const judge = new LLMJudge({
      provider: scriptedProvider([goodVerdict(0.8)], calls),
      model: "stub",
      rubric: "quality",
      maxTokens: 2222,
    });
    await judge.evaluate({ taskPrompt: "t", agentOutput: "o" });
    expect(calls[0].maxTokens).toBe(2222);
  });

  it("defaults maxTokens to 4096", async () => {
    const calls: Array<{ maxTokens?: number }> = [];
    const judge = new LLMJudge({
      provider: scriptedProvider([goodVerdict(0.8)], calls),
      model: "stub",
      rubric: "quality",
    });
    await judge.evaluate({ taskPrompt: "t", agentOutput: "o" });
    expect(calls[0].maxTokens).toBe(4096);
  });

  it("excludes an unparseable sample from the average", async () => {
    const calls: Array<{ maxTokens?: number }> = [];
    // 3 samples; sample 2 fails both attempts (2 calls), so responses are:
    // sample1 good, sample2 garbage x2, sample3 good.
    const judge = new LLMJudge({
      provider: scriptedProvider([goodVerdict(0.8), garbage(), garbage(), goodVerdict(0.6)], calls),
      model: "stub",
      rubric: "quality",
      samples: 3,
    });
    const result = await judge.evaluate({ taskPrompt: "t", agentOutput: "o" });
    expect(result.score).toBeCloseTo(0.7);
  });

  it("names truncation when all samples length-stopped", async () => {
    const calls: Array<{ maxTokens?: number }> = [];
    const judge = new LLMJudge({
      provider: scriptedProvider([garbage("max_tokens")], calls),
      model: "stub",
      rubric: "quality",
    });
    const result = await judge.evaluate({ taskPrompt: "t", agentOutput: "o" });
    expect(result.score).toBe(0);
    expect(result.reasoning).toContain("truncated");
    expect(result.reasoning).toContain("max_tokens");
  });
});
