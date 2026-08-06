/**
 * AC-905: model-aware output-token clamp.
 * Mirrors Python's tests/test_output_token_budgets.py TestClampOutputTokens.
 */

import { describe, it, expect } from "vitest";
import { clampOutputTokens } from "../src/providers/token-caps.js";

describe("clampOutputTokens", () => {
  it("clamps a known-capped model", () => {
    expect(clampOutputTokens(100_000, "claude-3-haiku-20240307")).toBe(4096);
  });

  it("passes requested below the cap", () => {
    expect(clampOutputTokens(2000, "claude-3-haiku-20240307")).toBe(2000);
  });

  it("longest prefix wins", () => {
    expect(clampOutputTokens(100_000, "claude-3-5-sonnet-20241022")).toBe(8192);
  });

  it("unknown and absent models pass through", () => {
    expect(clampOutputTokens(100_000, "future-model-9000")).toBe(100_000);
    expect(clampOutputTokens(5000, undefined)).toBe(5000);
  });
});
