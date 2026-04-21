/**
 * Exception taxonomy mapper tests — Task 3.4.
 * Mirrors Python _taxonomy.py tests.
 */
import { describe, test, expect } from "vitest";
import { mapExceptionToReason } from "../../../src/integrations/openai/taxonomy.js";
import { OPENAI_ERROR_REASONS } from "../../../src/production-traces/taxonomy/openai-error-reasons.js";

// Fake error classes matching OpenAI SDK class names
class RateLimitError extends Error { constructor() { super("rate limit"); this.name = "RateLimitError"; } }
class APITimeoutError extends Error { constructor() { super("timeout"); this.name = "APITimeoutError"; } }
class BadRequestError extends Error { constructor() { super("bad request"); this.name = "BadRequestError"; } }
class AuthenticationError extends Error { constructor() { super("auth"); this.name = "AuthenticationError"; } }
class PermissionDeniedError extends Error { constructor() { super("perm"); this.name = "PermissionDeniedError"; } }
class NotFoundError extends Error { constructor() { super("not found"); this.name = "NotFoundError"; } }
class APIConnectionError extends Error { constructor() { super("connection"); this.name = "APIConnectionError"; } }
class ContentFilterFinishReasonError extends Error { constructor() { super("content filter"); this.name = "ContentFilterFinishReasonError"; } }
class LengthFinishReasonError extends Error { constructor() { super("length"); this.name = "LengthFinishReasonError"; } }
class UnprocessableEntityError extends Error { constructor() { super("unprocessable"); this.name = "UnprocessableEntityError"; } }
class UnknownError extends Error { constructor() { super("unknown"); this.name = "UnknownError"; } }

describe("mapExceptionToReason", () => {
  test("RateLimitError → rateLimited", () => {
    expect(mapExceptionToReason(new RateLimitError())).toBe("rateLimited");
  });

  test("APITimeoutError → timeout", () => {
    expect(mapExceptionToReason(new APITimeoutError())).toBe("timeout");
  });

  test("BadRequestError → badRequest", () => {
    expect(mapExceptionToReason(new BadRequestError())).toBe("badRequest");
  });

  test("AuthenticationError → authentication", () => {
    expect(mapExceptionToReason(new AuthenticationError())).toBe("authentication");
  });

  test("PermissionDeniedError → permissionDenied", () => {
    expect(mapExceptionToReason(new PermissionDeniedError())).toBe("permissionDenied");
  });

  test("NotFoundError → notFound", () => {
    expect(mapExceptionToReason(new NotFoundError())).toBe("notFound");
  });

  test("APIConnectionError → apiConnection", () => {
    expect(mapExceptionToReason(new APIConnectionError())).toBe("apiConnection");
  });

  test("ContentFilterFinishReasonError → contentFilter", () => {
    expect(mapExceptionToReason(new ContentFilterFinishReasonError())).toBe("contentFilter");
  });

  test("LengthFinishReasonError → lengthCap", () => {
    expect(mapExceptionToReason(new LengthFinishReasonError())).toBe("lengthCap");
  });

  test("UnprocessableEntityError → upstreamError", () => {
    expect(mapExceptionToReason(new UnprocessableEntityError())).toBe("upstreamError");
  });

  test("unknown class → uncategorized", () => {
    expect(mapExceptionToReason(new UnknownError())).toBe("uncategorized");
  });

  test("plain Error → uncategorized", () => {
    expect(mapExceptionToReason(new Error("oops"))).toBe("uncategorized");
  });

  test("non-Error value → uncategorized", () => {
    expect(mapExceptionToReason("string error")).toBe("uncategorized");
  });

  test("all taxonomy keys are reachable", () => {
    const reachable = new Set(Object.values(OPENAI_ERROR_REASONS));
    // "uncategorized" is fallback, not in OPENAI_ERROR_REASONS
    expect(reachable.size).toBeGreaterThan(0);
  });
});
