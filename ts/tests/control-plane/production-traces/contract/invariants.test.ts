import { describe, test, expect } from "vitest";
import {
  validateTimingSanity,
  validateJsonPointer,
  validateRedactionPaths,
} from "../../../../src/production-traces/contract/invariants.js";
import type { ProductionTrace, TimingInfo } from "../../../../src/production-traces/contract/types.js";

// Minimal valid trace fixture, augmentable per test.
function baseTrace(): ProductionTrace {
  return {
    schemaVersion: "1.0",
    traceId: "01KFDQ9XZ3M7RT2V8K1PHY4BNC" as ProductionTrace["traceId"],
    source: { emitter: "sdk", sdk: { name: "ts", version: "0.4.3" } },
    provider: { name: "openai" },
    model: "gpt-4o-mini",
    env: {
      environmentTag: "production" as ProductionTrace["env"]["environmentTag"],
      appId: "my-app" as ProductionTrace["env"]["appId"],
    },
    messages: [{ role: "user", content: "hello", timestamp: "2026-04-17T12:00:00.000Z" }],
    toolCalls: [],
    timing: {
      startedAt: "2026-04-17T12:00:00.000Z",
      endedAt: "2026-04-17T12:00:01.000Z",
      latencyMs: 1000,
    },
    usage: { tokensIn: 10, tokensOut: 5 },
    feedbackRefs: [],
    links: {},
    redactions: [],
  };
}

describe("validateTimingSanity", () => {
  test("accepts endedAt > startedAt with matching latencyMs", () => {
    const t: TimingInfo = {
      startedAt: "2026-04-17T12:00:00.000Z",
      endedAt: "2026-04-17T12:00:01.000Z",
      latencyMs: 1000,
    };
    expect(validateTimingSanity(t).valid).toBe(true);
  });

  test("accepts equal timestamps and zero latency", () => {
    const t: TimingInfo = {
      startedAt: "2026-04-17T12:00:00.000Z",
      endedAt: "2026-04-17T12:00:00.000Z",
      latencyMs: 0,
    };
    expect(validateTimingSanity(t).valid).toBe(true);
  });

  test("rejects endedAt < startedAt", () => {
    const t: TimingInfo = {
      startedAt: "2026-04-17T12:00:01.000Z",
      endedAt: "2026-04-17T12:00:00.000Z",
      latencyMs: 0,
    };
    const r = validateTimingSanity(t);
    expect(r.valid).toBe(false);
    if (!r.valid) expect(r.errors.some((e) => /endedAt/.test(e))).toBe(true);
  });

  test("rejects negative latencyMs", () => {
    const t: TimingInfo = {
      startedAt: "2026-04-17T12:00:00.000Z",
      endedAt: "2026-04-17T12:00:01.000Z",
      latencyMs: -5,
    };
    const r = validateTimingSanity(t);
    expect(r.valid).toBe(false);
    if (!r.valid) expect(r.errors.some((e) => /latencyMs/.test(e))).toBe(true);
  });

  test("rejects unparseable timestamps", () => {
    const t: TimingInfo = {
      startedAt: "not-a-date",
      endedAt: "also-not",
      latencyMs: 10,
    };
    const r = validateTimingSanity(t);
    expect(r.valid).toBe(false);
  });
});

describe("validateJsonPointer", () => {
  const obj = {
    messages: [
      { role: "user", content: "hello" },
      { role: "assistant", content: "world" },
    ],
    metadata: { customer: "acme" },
  };

  test("resolves root / empty pointer to the document itself", () => {
    expect(validateJsonPointer(obj, "").valid).toBe(true);
  });

  test("resolves simple field path", () => {
    expect(validateJsonPointer(obj, "/metadata").valid).toBe(true);
    expect(validateJsonPointer(obj, "/metadata/customer").valid).toBe(true);
  });

  test("resolves array index path", () => {
    expect(validateJsonPointer(obj, "/messages/0").valid).toBe(true);
    expect(validateJsonPointer(obj, "/messages/1/content").valid).toBe(true);
  });

  test("rejects path that doesn't resolve", () => {
    expect(validateJsonPointer(obj, "/nonexistent").valid).toBe(false);
    expect(validateJsonPointer(obj, "/messages/5").valid).toBe(false);
    expect(validateJsonPointer(obj, "/messages/notanumber").valid).toBe(false);
  });

  test("rejects malformed pointer (missing leading slash on non-empty)", () => {
    expect(validateJsonPointer(obj, "messages/0").valid).toBe(false);
  });

  test("handles escaped tokens (~0 = '~', ~1 = '/') per RFC 6901", () => {
    const escaped = { "a/b": 1, "c~d": 2 };
    expect(validateJsonPointer(escaped, "/a~1b").valid).toBe(true);
    expect(validateJsonPointer(escaped, "/c~0d").valid).toBe(true);
  });
});

describe("validateRedactionPaths", () => {
  test("accepts trace with no redactions", () => {
    expect(validateRedactionPaths(baseTrace()).valid).toBe(true);
  });

  test("accepts trace whose redaction paths all resolve", () => {
    const t: ProductionTrace = {
      ...baseTrace(),
      redactions: [
        {
          path: "/messages/0/content",
          reason: "pii-email",
          detectedBy: "ingestion",
          detectedAt: "2026-04-17T12:00:02.000Z",
        },
        {
          path: "/model",
          reason: "pii-custom",
          detectedBy: "client",
          detectedAt: "2026-04-17T12:00:02.000Z",
        },
      ],
    };
    expect(validateRedactionPaths(t).valid).toBe(true);
  });

  test("rejects trace with a redaction path that does not resolve", () => {
    const t: ProductionTrace = {
      ...baseTrace(),
      redactions: [
        {
          path: "/messages/42/content",
          reason: "pii-email",
          detectedBy: "ingestion",
          detectedAt: "2026-04-17T12:00:02.000Z",
        },
      ],
    };
    const r = validateRedactionPaths(t);
    expect(r.valid).toBe(false);
    if (!r.valid) expect(r.errors.some((e) => /messages/.test(e))).toBe(true);
  });
});
