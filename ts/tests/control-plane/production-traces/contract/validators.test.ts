import { describe, test, expect } from "vitest";
import {
  validateProductionTrace,
  validateTraceSource,
  validateSession,
  validateEnvContext,
  validateTimingInfo,
  validateUsageInfo,
  validateProductionOutcome,
  validateFeedbackRef,
  validateTraceLinks,
  validateRedactionMarker,
} from "../../../../src/production-traces/contract/validators.js";

// A minimal valid ProductionTrace used as the fixture-of-record in this suite.
// Uses a real ULID (generated with `ulid`) that excludes I/L/O/U.
const VALID_TRACE_ID = "01KFDQ9XZ3M7RT2V8K1PHY4BNC";

const validTrace = {
  schemaVersion: "1.0",
  traceId: VALID_TRACE_ID,
  source: {
    emitter: "sdk",
    sdk: { name: "autocontext-ts", version: "0.4.3" },
  },
  provider: {
    name: "openai",
  },
  model: "gpt-4o-mini",
  env: {
    environmentTag: "production",
    appId: "my-app",
  },
  messages: [
    { role: "user", content: "hello", timestamp: "2026-04-17T12:00:00.000Z" },
  ],
  toolCalls: [],
  timing: {
    startedAt: "2026-04-17T12:00:00.000Z",
    endedAt: "2026-04-17T12:00:01.000Z",
    latencyMs: 1000,
  },
  usage: {
    tokensIn: 10,
    tokensOut: 5,
  },
  feedbackRefs: [],
  links: {},
  redactions: [],
};

describe("validateProductionTrace", () => {
  test("accepts a minimal valid trace", () => {
    const r = validateProductionTrace(validTrace);
    if (!r.valid) {
      // If invalid, dump errors to fail loudly with context.
      // eslint-disable-next-line no-console
      console.error(r.errors);
    }
    expect(r.valid).toBe(true);
  });

  test("rejects missing schemaVersion", () => {
    const { schemaVersion: _sv, ...bad } = validTrace;
    const r = validateProductionTrace(bad);
    expect(r.valid).toBe(false);
  });

  test("rejects invalid ULID traceId (lowercase / contains I)", () => {
    const bad = { ...validTrace, traceId: "01kfdq9xz3m7rt2v8k1phy4bnc" };
    expect(validateProductionTrace(bad).valid).toBe(false);
    const badI = { ...validTrace, traceId: "01KFDQ9XZ3M7RT2V8K1PHY4BNI" };
    expect(validateProductionTrace(badI).valid).toBe(false);
  });

  test("rejects provider.name not in enum", () => {
    const bad = { ...validTrace, provider: { name: "aliens" } };
    expect(validateProductionTrace(bad).valid).toBe(false);
  });

  test("rejects empty messages array", () => {
    const bad = { ...validTrace, messages: [] };
    expect(validateProductionTrace(bad).valid).toBe(false);
  });

  test("rejects message with role not in enum", () => {
    const bad = {
      ...validTrace,
      messages: [{ role: "wizard", content: "hi", timestamp: "2026-04-17T12:00:00.000Z" }],
    };
    expect(validateProductionTrace(bad).valid).toBe(false);
  });

  test("accepts trace with optional fields populated (session, outcome, feedback, metadata)", () => {
    const rich = {
      ...validTrace,
      session: {
        userIdHash: "a".repeat(64),
        sessionIdHash: "b".repeat(64),
        requestId: "req-123",
      },
      outcome: {
        label: "success",
        score: 0.92,
        reasoning: "completed task cleanly",
      },
      feedbackRefs: [
        {
          kind: "thumbs",
          submittedAt: "2026-04-17T12:05:00.000Z",
          ref: "fb-1",
        },
      ],
      links: {
        scenarioId: "checkout-flow",
        runId: "run-42",
      },
      redactions: [
        {
          path: "/messages/0/content",
          reason: "pii-email",
          detectedBy: "ingestion",
          detectedAt: "2026-04-17T12:00:02.000Z",
        },
      ],
      metadata: { customer: "acme-corp" },
    };
    const r = validateProductionTrace(rich);
    if (!r.valid) {
      // eslint-disable-next-line no-console
      console.error(r.errors);
    }
    expect(r.valid).toBe(true);
  });

  test("rejects outcome.label outside enum", () => {
    const bad = { ...validTrace, outcome: { label: "kinda-ok" } };
    expect(validateProductionTrace(bad).valid).toBe(false);
  });

  test("rejects redaction marker missing required fields", () => {
    const bad = {
      ...validTrace,
      redactions: [{ path: "/x", detectedBy: "client" }], // missing reason, detectedAt
    };
    expect(validateProductionTrace(bad).valid).toBe(false);
  });

  test("rejects tokensIn negative", () => {
    const bad = { ...validTrace, usage: { tokensIn: -1, tokensOut: 5 } };
    expect(validateProductionTrace(bad).valid).toBe(false);
  });
});

describe("per-document validators", () => {
  test("validateTraceSource accepts valid / rejects missing sdk", () => {
    expect(validateTraceSource({ emitter: "sdk", sdk: { name: "x", version: "1" } }).valid).toBe(true);
    expect(validateTraceSource({ emitter: "sdk" }).valid).toBe(false);
  });

  test("validateSession accepts all-optional and rejects bad hash", () => {
    expect(validateSession({}).valid).toBe(true);
    expect(validateSession({ userIdHash: "a".repeat(64) }).valid).toBe(true);
    expect(validateSession({ userIdHash: "NOTHEX".padEnd(64, "X") }).valid).toBe(false);
  });

  test("validateEnvContext requires environmentTag and appId", () => {
    expect(validateEnvContext({ environmentTag: "production", appId: "my-app" }).valid).toBe(true);
    expect(validateEnvContext({ environmentTag: "production" }).valid).toBe(false);
    expect(validateEnvContext({ appId: "Bad App" }).valid).toBe(false);
  });

  test("validateTimingInfo requires startedAt, endedAt, latencyMs", () => {
    expect(
      validateTimingInfo({
        startedAt: "2026-04-17T12:00:00.000Z",
        endedAt: "2026-04-17T12:00:01.000Z",
        latencyMs: 1000,
      }).valid,
    ).toBe(true);
    expect(validateTimingInfo({ startedAt: "2026-04-17T12:00:00.000Z" }).valid).toBe(false);
  });

  test("validateUsageInfo requires tokensIn/out non-negative integers", () => {
    expect(validateUsageInfo({ tokensIn: 0, tokensOut: 0 }).valid).toBe(true);
    expect(validateUsageInfo({ tokensIn: -1, tokensOut: 0 }).valid).toBe(false);
    expect(validateUsageInfo({ tokensIn: 1.5, tokensOut: 0 }).valid).toBe(false);
  });

  test("validateProductionOutcome accepts empty object (all optional)", () => {
    expect(validateProductionOutcome({}).valid).toBe(true);
    expect(validateProductionOutcome({ label: "success", score: 0.5 }).valid).toBe(true);
    expect(validateProductionOutcome({ label: "made-up" }).valid).toBe(false);
  });

  test("validateFeedbackRef requires kind, submittedAt, ref", () => {
    expect(
      validateFeedbackRef({
        kind: "thumbs",
        submittedAt: "2026-04-17T12:00:00.000Z",
        ref: "fb-1",
      }).valid,
    ).toBe(true);
    expect(validateFeedbackRef({ kind: "thumbs" }).valid).toBe(false);
  });

  test("validateTraceLinks accepts empty and valid scenarioId", () => {
    expect(validateTraceLinks({}).valid).toBe(true);
    expect(validateTraceLinks({ scenarioId: "grid_ctf" }).valid).toBe(true);
    expect(validateTraceLinks({ scenarioId: "BadCaps" }).valid).toBe(false);
  });

  test("validateRedactionMarker requires path, reason, detectedBy, detectedAt", () => {
    expect(
      validateRedactionMarker({
        path: "/messages/0/content",
        reason: "pii-email",
        detectedBy: "ingestion",
        detectedAt: "2026-04-17T12:00:00.000Z",
      }).valid,
    ).toBe(true);
    expect(validateRedactionMarker({ path: "/x", reason: "pii-email" }).valid).toBe(false);
    expect(
      validateRedactionMarker({
        path: "/x",
        reason: "unknown-category",
        detectedBy: "ingestion",
        detectedAt: "2026-04-17T12:00:00.000Z",
      }).valid,
    ).toBe(false);
  });
});

describe("round-trip: encode → parse → validate → deep-equal", () => {
  test("ProductionTrace survives JSON round-trip", () => {
    const json = JSON.stringify(validTrace);
    const parsed = JSON.parse(json);
    const r = validateProductionTrace(parsed);
    expect(r.valid).toBe(true);
    expect(parsed).toStrictEqual(validTrace);
  });
});
