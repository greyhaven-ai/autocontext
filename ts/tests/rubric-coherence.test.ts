import { describe, it, expect } from "vitest";
import { checkRubricCoherence } from "../src/judge/rubric-coherence.js";

describe("checkRubricCoherence", () => {
  it("detects contradictory adjective pairs", () => {
    const result = checkRubricCoherence("Write a simple yet complex analysis of the topic");
    expect(result.isCoherent).toBe(false);
    expect(result.warnings.length).toBeGreaterThanOrEqual(1);
    expect(result.warnings.some(w => w.includes("simple") && w.includes("complex"))).toBe(true);
  });

  it("detects vague rubric with many generic terms", () => {
    const result = checkRubricCoherence(
      "Evaluate good quality and appropriate content with nice output and proper formatting",
    );
    expect(result.isCoherent).toBe(false);
    expect(result.warnings.some(w => w.includes("vague"))).toBe(true);
  });

  it("detects underspecified short rubric", () => {
    const result = checkRubricCoherence("Score the output");
    expect(result.isCoherent).toBe(false);
    expect(result.warnings.some(w => w.includes("underspecified"))).toBe(true);
  });

  it("passes a clean well-specified rubric", () => {
    const result = checkRubricCoherence(
      "Evaluate factual accuracy against provided references. " +
        "Check code correctness by verifying all test cases pass. " +
        "Assess clarity of explanation on a 0-1 scale.",
    );
    expect(result.isCoherent).toBe(true);
    expect(result.warnings).toHaveLength(0);
  });

  it("accumulates multiple warnings", () => {
    const result = checkRubricCoherence(
      "Be brief and comprehensive with good nice appropriate adequate proper quality output",
    );
    expect(result.isCoherent).toBe(false);
    // Should have at least: contradictory (brief/comprehensive) + vague terms
    expect(result.warnings.length).toBeGreaterThanOrEqual(2);
  });

  it("detects multiple contradictory pairs", () => {
    const result = checkRubricCoherence(
      "Write a simple and complex analysis that is both concise and detailed in its approach",
    );
    expect(result.isCoherent).toBe(false);
    const contradictionWarnings = result.warnings.filter(w => w.includes("contradictory"));
    expect(contradictionWarnings.length).toBeGreaterThanOrEqual(2);
  });
});
