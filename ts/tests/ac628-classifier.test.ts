/**
 * AC-628 — TS parity tests for LLM-primary family classifier with config-driven
 * fast-path threshold.
 *
 * Mirrors `autocontext/tests/test_ac628_classifier.py`.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  classifyScenarioFamily,
  LowConfidenceError,
  type LlmFn,
} from "../src/scenarios/family-classifier.js";

const HIGH_SIGNAL_DESCRIPTION = "negotiate price with the supplier and reach a deal";
const ZERO_SIGNAL_DESCRIPTION = "xyz plop qux widget zzzz";

const ENV_KEY = "AUTOCONTEXT_CLASSIFIER_FAST_PATH_THRESHOLD";

describe("AC-628: classification fields", () => {
  it("does not expose legacy llmFallbackUsed field on classification", () => {
    const c = classifyScenarioFamily(HIGH_SIGNAL_DESCRIPTION);
    expect(c).not.toHaveProperty("llmFallbackUsed");
    expect(c).not.toHaveProperty("llmFallbackAttempted");
  });

  it("exposes llmClassifierUsed=true when LLM picks the family on ambiguous input", () => {
    // Ambiguous: split signals (evaluate→agent_task, trace→simulation), confidence below threshold.
    const ambiguous = "evaluate some data and trace results";
    const llmFn: LlmFn = () =>
      '{"family": "simulation", "confidence": 0.7, "rationale": "llm picked simulation"}';
    const c = classifyScenarioFamily(ambiguous, { llmFn });
    expect(c.llmClassifierUsed).toBe(true);
    expect(c.familyName).toBe("simulation");
  });

  it("does not set llmClassifierUsed on the fast path", () => {
    const c = classifyScenarioFamily(HIGH_SIGNAL_DESCRIPTION);
    expect(c.llmClassifierUsed).toBeFalsy();
  });
});

describe("AC-628: fast-path threshold from env", () => {
  const originalThreshold = process.env[ENV_KEY];

  afterEach(() => {
    if (originalThreshold === undefined) {
      delete process.env[ENV_KEY];
    } else {
      process.env[ENV_KEY] = originalThreshold;
    }
  });

  it("uses default threshold 0.65 when env var is unset", () => {
    delete process.env[ENV_KEY];
    let llmCalled = false;
    const llmFn: LlmFn = () => {
      llmCalled = true;
      return "";
    };
    // High-signal description should clear default 0.65 → fast-path.
    classifyScenarioFamily(HIGH_SIGNAL_DESCRIPTION, { llmFn });
    expect(llmCalled).toBe(false);
  });

  it("respects a high custom threshold by routing ambiguous descriptions to LLM", () => {
    // "evaluate ... trace" splits across agent_task + simulation → ~0.5 confidence,
    // which clears default 0.65? No: 0.5 < 0.65 → ambiguous → LLM. We bump threshold
    // to 0.99 to ensure ambiguous descriptions can't fast-path under any tweak.
    process.env[ENV_KEY] = "0.99";
    let llmCalled = false;
    const llmFn: LlmFn = () => {
      llmCalled = true;
      return '{"family": "simulation", "confidence": 0.9, "rationale": "ok"}';
    };
    classifyScenarioFamily("evaluate some data and trace results", { llmFn });
    expect(llmCalled).toBe(true);
  });

  it("respects a low threshold by skipping LLM even on ambiguous descriptions", () => {
    process.env[ENV_KEY] = "0.05";
    let llmCalled = false;
    const llmFn: LlmFn = () => {
      llmCalled = true;
      return "";
    };
    classifyScenarioFamily("evaluate some data and trace results", { llmFn });
    expect(llmCalled).toBe(false);
  });
});

describe("AC-628: fast-path skips LLM on high-signal input", () => {
  it("does not invoke llmFn when keyword confidence clears threshold", () => {
    let llmCalls = 0;
    const llmFn: LlmFn = () => {
      llmCalls += 1;
      return "";
    };
    classifyScenarioFamily(HIGH_SIGNAL_DESCRIPTION, { llmFn });
    expect(llmCalls).toBe(0);
  });

  it("returns the keyword-derived family on the fast path (no LLM)", () => {
    const c = classifyScenarioFamily(HIGH_SIGNAL_DESCRIPTION);
    expect(c.familyName).toBe("negotiation");
    expect(c.noSignalsMatched).toBe(false);
  });
});

describe("AC-628: ambiguous descriptions invoke LLM when provided", () => {
  const AMBIGUOUS = "evaluate some data and trace results";

  it("calls llmFn exactly once on ambiguous input", () => {
    let llmCalls = 0;
    const llmFn: LlmFn = () => {
      llmCalls += 1;
      return '{"family": "simulation", "confidence": 0.6, "rationale": "ok"}';
    };
    classifyScenarioFamily(AMBIGUOUS, { llmFn });
    expect(llmCalls).toBe(1);
  });

  it("returns LLM-picked family on ambiguous + parseable LLM response", () => {
    const llmFn: LlmFn = () =>
      '{"family": "simulation", "confidence": 0.7, "rationale": "picked"}';
    const c = classifyScenarioFamily(AMBIGUOUS, { llmFn });
    expect(c.familyName).toBe("simulation");
    expect(c.llmClassifierUsed).toBe(true);
  });

  it("falls back to keyword result on ambiguous + bad LLM response", () => {
    const llmFn: LlmFn = () => "not json at all";
    const c = classifyScenarioFamily(AMBIGUOUS, { llmFn });
    expect(c.llmClassifierUsed).toBeFalsy();
    expect(c.llmClassifierAttempted).toBe(true);
  });
});

describe("AC-628: zero-signal behaviour", () => {
  it("raises LowConfidenceError when no keywords match and no llmFn is provided", () => {
    expect(() => classifyScenarioFamily(ZERO_SIGNAL_DESCRIPTION)).toThrow(LowConfidenceError);
  });

  it("attaches a classification with noSignalsMatched=true to the error", () => {
    try {
      classifyScenarioFamily(ZERO_SIGNAL_DESCRIPTION);
      throw new Error("expected LowConfidenceError");
    } catch (e) {
      expect(e).toBeInstanceOf(LowConfidenceError);
      const err = e as LowConfidenceError;
      expect(err.classification.noSignalsMatched).toBe(true);
    }
  });

  it("returns LLM result when zero-signal + llmFn succeeds", () => {
    const llmFn: LlmFn = () =>
      '{"family": "simulation", "confidence": 0.82, "rationale": "llm rescued zero-signal"}';
    const c = classifyScenarioFamily(ZERO_SIGNAL_DESCRIPTION, { llmFn });
    expect(c.familyName).toBe("simulation");
    expect(c.llmClassifierUsed).toBe(true);
    expect(c.noSignalsMatched).toBe(false);
  });

  it("raises LowConfidenceError with llmClassifierAttempted=true when zero-signal + llmFn fails", () => {
    const llmFn: LlmFn = () => "not json";
    try {
      classifyScenarioFamily(ZERO_SIGNAL_DESCRIPTION, { llmFn });
      throw new Error("expected LowConfidenceError");
    } catch (e) {
      expect(e).toBeInstanceOf(LowConfidenceError);
      const err = e as LowConfidenceError;
      expect(err.classification.noSignalsMatched).toBe(true);
      expect(err.classification.llmClassifierAttempted).toBe(true);
    }
  });

  it("does not call llmFn when keywords match (only zero-signal triggers required-LLM)", () => {
    let llmCalls = 0;
    const llmFn: LlmFn = () => {
      llmCalls += 1;
      return "";
    };
    classifyScenarioFamily(HIGH_SIGNAL_DESCRIPTION, { llmFn });
    expect(llmCalls).toBe(0);
  });
});
