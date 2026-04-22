import { describe, test, expect } from "vitest";
import {
  OUTCOME_REASON_KEYS,
  OPENAI_ERROR_REASON_KEYS,
} from "../../../src/production-traces/taxonomy/index.js";

describe("cross-provider shared OutcomeReasonKey union", () => {
  test("includes all provider keys + uncategorized + overloaded", () => {
    expect(new Set(OUTCOME_REASON_KEYS)).toEqual(new Set([
      "rateLimited", "timeout", "badRequest", "authentication",
      "permissionDenied", "notFound", "apiConnection", "contentFilter",
      "lengthCap", "upstreamError", "overloaded", "uncategorized",
    ]));
  });

  test("openai keys are a subset of shared keys", () => {
    for (const key of OPENAI_ERROR_REASON_KEYS) {
      expect(OUTCOME_REASON_KEYS).toContain(key);
    }
  });
});
