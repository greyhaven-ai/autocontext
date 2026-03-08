/**
 * Tests for factual_confidence dimension support (MTS-50).
 */
import { describe, it, expect } from "vitest";
import { LLMJudge } from "../src/judge/index.js";
import type { LLMProvider, CompletionResult } from "../src/types/index.js";

function makeMockProvider(response: string): LLMProvider {
  return {
    complete: async (): Promise<CompletionResult> => ({
      text: response,
      model: "test",
      usage: { inputTokens: 0, outputTokens: 0 },
    }),
  };
}

describe("factual_confidence dimension", () => {
  it("returns factual_confidence when judge provides it", async () => {
    const provider = makeMockProvider(
      '<!-- JUDGE_RESULT_START -->\n{"score": 0.7, "reasoning": "decent", "dimensions": {"factual_accuracy": 0.8, "factual_confidence": 0.9, "clarity": 0.6}}\n<!-- JUDGE_RESULT_END -->',
    );
    const judge = new LLMJudge({ provider, model: "test", rubric: "Evaluate." });
    const result = await judge.evaluate({
      taskPrompt: "Summarize.",
      agentOutput: "Output.",
      referenceContext: "Source doc.",
    });
    expect(result.dimensionScores.factual_confidence).toBe(0.9);
  });

  it("defaults factual_confidence to 0.5 when judge omits it", async () => {
    const provider = makeMockProvider(
      '<!-- JUDGE_RESULT_START -->\n{"score": 0.7, "reasoning": "ok", "dimensions": {"factual_accuracy": 0.8, "clarity": 0.6}}\n<!-- JUDGE_RESULT_END -->',
    );
    const judge = new LLMJudge({ provider, model: "test", rubric: "Evaluate." });
    const result = await judge.evaluate({
      taskPrompt: "Summarize.",
      agentOutput: "Output.",
      referenceContext: "Source doc.",
    });
    expect(result.dimensionScores.factual_confidence).toBe(0.5);
  });

  it("does not inject factual_confidence without reference context", async () => {
    const provider = makeMockProvider(
      '<!-- JUDGE_RESULT_START -->\n{"score": 0.7, "reasoning": "ok", "dimensions": {"clarity": 0.6}}\n<!-- JUDGE_RESULT_END -->',
    );
    const judge = new LLMJudge({ provider, model: "test", rubric: "Evaluate." });
    const result = await judge.evaluate({
      taskPrompt: "Write a poem.",
      agentOutput: "Roses are red.",
    });
    expect(result.dimensionScores.factual_confidence).toBeUndefined();
    expect(result.dimensionScores.factual_accuracy).toBeUndefined();
  });

  it("includes factual_confidence instruction in system prompt", async () => {
    const captured: string[] = [];
    const provider: LLMProvider = {
      complete: async (opts): Promise<CompletionResult> => {
        captured.push(opts.systemPrompt ?? "");
        return {
          text: '<!-- JUDGE_RESULT_START -->\n{"score": 0.5, "reasoning": "ok", "dimensions": {}}\n<!-- JUDGE_RESULT_END -->',
          model: "test",
          usage: { inputTokens: 0, outputTokens: 0 },
        };
      },
    };
    const judge = new LLMJudge({ provider, model: "test", rubric: "Check facts." });
    await judge.evaluate({
      taskPrompt: "Summarize.",
      agentOutput: "Output.",
      referenceContext: "Source doc.",
    });
    expect(captured).toHaveLength(1);
    expect(captured[0]).toContain("factual_confidence");
  });
});
