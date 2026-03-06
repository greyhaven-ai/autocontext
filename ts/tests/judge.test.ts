import { describe, it, expect } from "vitest";
import { LLMJudge } from "../src/judge/index.js";
import type { LLMProvider, CompletionResult } from "../src/types/index.js";

function makeMockProvider(response: string): LLMProvider {
  return {
    name: "mock",
    defaultModel: () => "mock-model",
    complete: async () => ({ text: response, usage: {} }),
  };
}

describe("LLMJudge", () => {
  it("evaluates with marker response", async () => {
    const provider = makeMockProvider(
      '<!-- JUDGE_RESULT_START -->\n{"score": 0.85, "reasoning": "Well done", "dimensions": {"clarity": 0.9}}\n<!-- JUDGE_RESULT_END -->',
    );
    const judge = new LLMJudge({ provider, model: "test", rubric: "Be clear" });
    const result = await judge.evaluate({
      taskPrompt: "Write something",
      agentOutput: "Hello world",
    });
    expect(result.score).toBe(0.85);
    expect(result.reasoning).toContain("Well done");
    expect(result.reasoning).not.toContain("[raw_json parse]");
    expect(result.dimensionScores.clarity).toBe(0.9);
    expect(result.parseMethod).toBe("raw_json"); // raw_json tried first, matches JSON in markers
    expect(result.internalRetries).toBe(0);
  });

  it("retries on parse failure and tracks internalRetries", async () => {
    let callCount = 0;
    const provider: LLMProvider = {
      name: "retry-mock",
      defaultModel: () => "m",
      complete: async () => {
        callCount++;
        if (callCount === 1) return { text: "no structured output here", usage: {} };
        return {
          text: '<!-- JUDGE_RESULT_START -->\n{"score": 0.7, "reasoning": "OK"}\n<!-- JUDGE_RESULT_END -->',
          usage: {},
        };
      },
    };
    const judge = new LLMJudge({ provider, model: "m", rubric: "r" });
    const result = await judge.evaluate({ taskPrompt: "t", agentOutput: "o" });
    expect(result.score).toBe(0.7);
    expect(callCount).toBe(2);
    expect(result.internalRetries).toBe(1);
  });

  it("adds factual_accuracy when reference context provided", async () => {
    const provider = makeMockProvider(
      '<!-- JUDGE_RESULT_START -->\n{"score": 0.6, "reasoning": "meh"}\n<!-- JUDGE_RESULT_END -->',
    );
    const judge = new LLMJudge({ provider, model: "m", rubric: "r" });
    const result = await judge.evaluate({
      taskPrompt: "t",
      agentOutput: "o",
      referenceContext: "The truth",
    });
    expect(result.dimensionScores.factual_accuracy).toBe(0.6);
  });

  it("averages multiple samples", async () => {
    let call = 0;
    const provider: LLMProvider = {
      name: "multi",
      defaultModel: () => "m",
      complete: async () => {
        call++;
        const score = call === 1 ? 0.8 : 0.6;
        return {
          text: `<!-- JUDGE_RESULT_START -->\n{"score": ${score}, "reasoning": "s${call}"}\n<!-- JUDGE_RESULT_END -->`,
          usage: {},
        };
      },
    };
    const judge = new LLMJudge({ provider, model: "m", rubric: "r", samples: 2 });
    const result = await judge.evaluate({ taskPrompt: "t", agentOutput: "o" });
    expect(result.score).toBe(0.7);
    expect(result.rawResponses).toHaveLength(2);
    expect(result.internalRetries).toBe(0);
  });

  it("exposes parseMethod from last sample", async () => {
    const provider = makeMockProvider(
      'The agent did well. Score: 0.8',
    );
    const judge = new LLMJudge({ provider, model: "m", rubric: "r" });
    const result = await judge.evaluate({ taskPrompt: "t", agentOutput: "o" });
    expect(result.parseMethod).toBe("plaintext");
  });
});
