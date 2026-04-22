import { describe, test, expect } from "vitest";
import {
  ANTHROPIC_ERROR_REASONS,
  ANTHROPIC_ERROR_REASON_KEYS,
} from "../../../src/production-traces/taxonomy/anthropic-error-reasons.js";

describe("Anthropic error-reason taxonomy", () => {
  test("table has all locked keys", () => {
    expect(new Set(ANTHROPIC_ERROR_REASON_KEYS)).toEqual(new Set([
      "rateLimited", "timeout", "badRequest", "authentication",
      "permissionDenied", "notFound", "apiConnection", "overloaded",
      "upstreamError", "uncategorized",
    ]));
  });

  test("classes map to locked keys (byte-identical to Python half)", () => {
    expect(ANTHROPIC_ERROR_REASONS).toEqual({
      RateLimitError: "rateLimited",
      APITimeoutError: "timeout",
      BadRequestError: "badRequest",
      AuthenticationError: "authentication",
      PermissionDeniedError: "permissionDenied",
      NotFoundError: "notFound",
      APIConnectionError: "apiConnection",
      OverloadedError: "overloaded",
      ConflictError: "upstreamError",
      UnprocessableEntityError: "upstreamError",
      InternalServerError: "upstreamError",
      APIStatusError: "upstreamError",
      APIError: "upstreamError",
    });
  });

  test("table is frozen", () => {
    expect(Object.isFrozen(ANTHROPIC_ERROR_REASONS)).toBe(true);
  });
});
