import { describe, expect, it } from "vitest";
import { ResearchResult, Citation } from "../src/research/types.js";
import { ResearchBrief } from "../src/research/consultation.js";
import { EvalResult, ResearchEvaluator, BatchSummary } from "../src/research/evaluation.js";

function brief(n = 1, confidence = 0.8): ResearchBrief {
  const results = Array.from({ length: n }, (_, i) =>
    new ResearchResult({
      queryTopic: `topic-${i}`,
      summary: `Finding ${i}`,
      confidence,
      citations: [new Citation({ source: `src-${i}`, url: `https://ex.com/${i}`, relevance: 0.9 })],
    })
  );
  return ResearchBrief.fromResults("test", results);
}

describe("EvalResult", () => {
  it("detects improvement", () => {
    const r = new EvalResult({ baselineScore: 0.6, augmentedScore: 0.85, improvement: 0.25, citationCoverage: 0.9 });
    expect(r.isImprovement).toBe(true);
    expect(r.relativeGain).toBeCloseTo(0.4167, 2);
  });

  it("no improvement", () => {
    const r = new EvalResult({ baselineScore: 0.8, augmentedScore: 0.75, improvement: -0.05 });
    expect(r.isImprovement).toBe(false);
  });

  it("zero baseline", () => {
    const r = new EvalResult({ baselineScore: 0, augmentedScore: 0.5, improvement: 0.5 });
    expect(r.relativeGain).toBe(Infinity);
  });
});

describe("ResearchEvaluator", () => {
  it("evaluate pair", () => {
    const evaluator = new ResearchEvaluator();
    const result = evaluator.evaluatePair({
      brief: brief(),
      baseline: "Generic auth",
      augmented: "OAuth2 with RFC 7636",
      scoreFn: (t) => (t.includes("RFC") ? 0.9 : 0.5),
    });
    expect(result.isImprovement).toBe(true);
  });

  it("no improvement pair", () => {
    const evaluator = new ResearchEvaluator();
    const result = evaluator.evaluatePair({
      brief: brief(), baseline: "good", augmented: "also good", scoreFn: () => 0.8,
    });
    expect(result.isImprovement).toBe(false);
  });

  it("evaluate batch", () => {
    const evaluator = new ResearchEvaluator();
    const summary = evaluator.evaluateBatch({
      pairs: [
        { brief: brief(), baseline: "basic", augmented: "RFC backed" },
        { brief: brief(), baseline: "generic", augmented: "RFC source" },
      ],
      scoreFn: (t) => (t.includes("RFC") ? 0.9 : 0.5),
    });
    expect(summary.sampleSize).toBe(2);
    expect(summary.avgImprovement).toBeGreaterThan(0);
    expect(summary.winRate).toBeCloseTo(1.0);
  });

  it("empty batch", () => {
    const evaluator = new ResearchEvaluator();
    const summary = evaluator.evaluateBatch({ pairs: [], scoreFn: () => 0.5 });
    expect(summary.sampleSize).toBe(0);
  });

  it("citation coverage", () => {
    const evaluator = new ResearchEvaluator();
    const b = brief(2);
    const result = evaluator.evaluatePair({
      brief: b, baseline: "none", augmented: "According to src-0 and src-1",
      scoreFn: () => 0.7,
    });
    expect(result.citationCoverage).toBeCloseTo(1.0);
  });

  it("partial citation coverage", () => {
    const evaluator = new ResearchEvaluator();
    const b = brief(3);
    const result = evaluator.evaluatePair({
      brief: b, baseline: "none", augmented: "Only src-0 mentioned",
      scoreFn: () => 0.7,
    });
    expect(result.citationCoverage).toBeCloseTo(1 / 3, 1);
  });
});
