/**
 * Smoke test: single-round judge eval (AC-29).
 *
 * Validates basic wiring: judge scores, parses, and returns correctly
 * on a canned prompt+output with a mock provider.
 */

import { describe, it, expect } from "vitest";
import { LLMJudge } from "../src/judge/index.js";
import type { LLMProvider } from "../src/types/index.js";

function mockProvider(responseText: string): LLMProvider {
  return {
    name: "mock",
    defaultModel: () => "mock-v1",
    complete: async () => ({ text: responseText, model: "mock-v1", usage: {} }),
  };
}

const PROMPT = "Write a one-paragraph summary of what AutoContext does";
const OUTPUT =
  "AutoContext is an iterative strategy generation system that uses multi-agent " +
  "collaboration to evolve strategies through tournament matches and LLM " +
  "judge evaluation with Elo-based progression gating.";
const RUBRIC =
  "Evaluate on: accuracy (factual correctness), clarity (readability), completeness (coverage of key concepts)";

function makeResponse(
  score = 0.85,
  dims: Record<string, number> = { accuracy: 0.9, clarity: 0.85, completeness: 0.8 },
) {
  const data = {
    score,
    reasoning: "The summary accurately captures the core AutoContext loop.",
    dimensions: dims,
  };
  return `<!-- JUDGE_RESULT_START -->\n${JSON.stringify(data)}\n<!-- JUDGE_RESULT_END -->`;
}

describe("Smoke: single-round judge eval (AC-29)", () => {
  it("returns valid JudgeResult with score 0-1", async () => {
    const judge = new LLMJudge({ provider: mockProvider(makeResponse()), model: "m", rubric: RUBRIC });
    const r = await judge.evaluate({ taskPrompt: PROMPT, agentOutput: OUTPUT });
    expect(r.score).toBeGreaterThanOrEqual(0);
    expect(r.score).toBeLessThanOrEqual(1);
    expect(r.score).toBe(0.85);
  });

  it("all 3 dimensions scored independently", async () => {
    const judge = new LLMJudge({ provider: mockProvider(makeResponse()), model: "m", rubric: RUBRIC });
    const r = await judge.evaluate({ taskPrompt: PROMPT, agentOutput: OUTPUT });
    expect(Object.keys(r.dimensionScores)).toHaveLength(3);
    expect(r.dimensionScores.accuracy).toBe(0.9);
    expect(r.dimensionScores.clarity).toBe(0.85);
    expect(r.dimensionScores.completeness).toBe(0.8);
  });

  it("reasoning is non-empty and relevant", async () => {
    const judge = new LLMJudge({ provider: mockProvider(makeResponse()), model: "m", rubric: RUBRIC });
    const r = await judge.evaluate({ taskPrompt: PROMPT, agentOutput: OUTPUT });
    expect(r.reasoning.length).toBeGreaterThan(0);
    expect(r.reasoning).toContain("AutoContext");
  });

  it("parse succeeds on first attempt (markers)", async () => {
    const judge = new LLMJudge({ provider: mockProvider(makeResponse()), model: "m", rubric: RUBRIC });
    const r = await judge.evaluate({ taskPrompt: PROMPT, agentOutput: OUTPUT });
    expect(["markers", "raw_json"]).toContain(r.parseMethod); // depends on parser strategy order
  });

  it("different dimension scores are independent", async () => {
    const judge = new LLMJudge({
      provider: mockProvider(makeResponse(0.75, { accuracy: 0.9, clarity: 0.7, completeness: 0.5 })),
      model: "m",
      rubric: RUBRIC,
    });
    const r = await judge.evaluate({ taskPrompt: PROMPT, agentOutput: OUTPUT });
    expect(r.dimensionScores.accuracy).toBe(0.9);
    expect(r.dimensionScores.clarity).toBe(0.7);
    expect(r.dimensionScores.completeness).toBe(0.5);
  });
});
