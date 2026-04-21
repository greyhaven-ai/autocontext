/**
 * Snapshot + parity tests for the OpenAI error-taxonomy constants (TS half).
 */
import { describe, test, expect } from "vitest";
import {
  OPENAI_ERROR_REASONS,
  OPENAI_ERROR_REASON_KEYS,
  type OpenAiErrorReasonKey,
} from "../../../src/production-traces/taxonomy/openai-error-reasons.js";

describe("OpenAI error-reason taxonomy", () => {
  test("table has all locked keys", () => {
    const expectedKeys = new Set<OpenAiErrorReasonKey>([
      "rateLimited",
      "timeout",
      "badRequest",
      "authentication",
      "permissionDenied",
      "notFound",
      "apiConnection",
      "contentFilter",
      "lengthCap",
      "upstreamError",
      "uncategorized",
    ]);
    expect(new Set(OPENAI_ERROR_REASON_KEYS)).toEqual(expectedKeys);
  });

  test("classes map to locked keys (byte-identical to Python half)", () => {
    expect(OPENAI_ERROR_REASONS).toEqual({
      RateLimitError: "rateLimited",
      APITimeoutError: "timeout",
      BadRequestError: "badRequest",
      AuthenticationError: "authentication",
      PermissionDeniedError: "permissionDenied",
      NotFoundError: "notFound",
      APIConnectionError: "apiConnection",
      ContentFilterFinishReasonError: "contentFilter",
      LengthFinishReasonError: "lengthCap",
      UnprocessableEntityError: "upstreamError",
      ConflictError: "upstreamError",
      APIError: "upstreamError",
    });
  });

  test("table is frozen", () => {
    expect(Object.isFrozen(OPENAI_ERROR_REASONS)).toBe(true);
  });
});
