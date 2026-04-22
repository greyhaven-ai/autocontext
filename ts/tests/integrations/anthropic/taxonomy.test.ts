/**
 * taxonomy.test.ts — Tests for Anthropic exception → reason-key mapping.
 */
import { describe, test, expect } from "vitest";
import { mapExceptionToReason } from "../../../src/integrations/anthropic/taxonomy.js";

describe("mapExceptionToReason", () => {
  test("RateLimitError class maps to rateLimited", () => {
    class RateLimitError extends Error {}
    expect(mapExceptionToReason(new RateLimitError("rate limited"))).toBe("rateLimited");
  });

  test("OverloadedError class maps to overloaded", () => {
    class OverloadedError extends Error {}
    expect(mapExceptionToReason(new OverloadedError("overloaded"))).toBe("overloaded");
  });

  test("unknown error class maps to uncategorized", () => {
    class SomeRandomError extends Error {}
    expect(mapExceptionToReason(new SomeRandomError("unknown"))).toBe("uncategorized");
  });

  test("null maps to uncategorized", () => {
    expect(mapExceptionToReason(null)).toBe("uncategorized");
  });

  test("non-object maps to uncategorized", () => {
    expect(mapExceptionToReason("just a string")).toBe("uncategorized");
  });

  test("APITimeoutError maps to timeout", () => {
    class APITimeoutError extends Error {}
    expect(mapExceptionToReason(new APITimeoutError("timeout"))).toBe("timeout");
  });
});
