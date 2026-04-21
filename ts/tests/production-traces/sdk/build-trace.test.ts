import { describe, test, expect } from "vitest";
import { buildTrace } from "../../../src/production-traces/sdk/build-trace.js";
import { ValidationError } from "../../../src/production-traces/sdk/validate.js";
import type {
  AppId,
  EnvironmentTag,
  ProductionTraceId,
} from "../../../src/production-traces/contract/branded-ids.js";
import type {
  BuildTraceInputs,
} from "../../../src/production-traces/sdk/build-trace.js";

function validInputs(overrides: Partial<BuildTraceInputs> = {}): BuildTraceInputs {
  return {
    provider: "openai",
    model: "gpt-4o-mini",
    messages: [
      { role: "user", content: "hi", timestamp: "2026-04-17T12:00:00.000Z" },
    ],
    timing: {
      startedAt: "2026-04-17T12:00:00.000Z",
      endedAt: "2026-04-17T12:00:01.000Z",
      latencyMs: 1000,
    },
    usage: { tokensIn: 10, tokensOut: 5 },
    env: {
      environmentTag: "production" as EnvironmentTag,
      appId: "my-app" as AppId,
    },
    ...overrides,
  };
}

describe("buildTrace — happy path", () => {
  test("returns a ProductionTrace with schemaVersion '1.0'", () => {
    const trace = buildTrace(validInputs());
    expect(trace.schemaVersion).toBe("1.0");
  });

  test("wraps provider string as ProviderInfo { name }", () => {
    const trace = buildTrace(validInputs());
    expect(trace.provider).toEqual({ name: "openai" });
  });

  test("auto-generates a ULID traceId when none provided", () => {
    const trace = buildTrace(validInputs());
    expect(typeof trace.traceId).toBe("string");
    // ULID: 26 Crockford base32 chars
    expect(/^[0-9A-HJKMNP-TV-Z]{26}$/.test(trace.traceId)).toBe(true);
  });

  test("honors an injected traceId verbatim", () => {
    const fixed = "01HZ6X2K7M9A3B4C5D6E7F8G9H" as ProductionTraceId;
    const trace = buildTrace(validInputs({ traceId: fixed }));
    expect(trace.traceId).toBe(fixed);
  });

  test("default source mirrors Python _default_source (emitter='sdk', sdk.name='autocontext-ts')", () => {
    const trace = buildTrace(validInputs());
    expect(trace.source.emitter).toBe("sdk");
    expect(trace.source.sdk.name).toBe("autocontext-ts");
    expect(typeof trace.source.sdk.version).toBe("string");
  });

  test("honors an injected source verbatim", () => {
    const source = { emitter: "my-svc", sdk: { name: "my-sdk", version: "9.9.9" } };
    const trace = buildTrace(validInputs({ source }));
    expect(trace.source).toEqual(source);
  });

  test("empty defaults for toolCalls, feedbackRefs, redactions, links", () => {
    const trace = buildTrace(validInputs());
    expect(trace.toolCalls).toEqual([]);
    expect(trace.feedbackRefs).toEqual([]);
    expect(trace.redactions).toEqual([]);
    expect(trace.links).toEqual({});
  });

  test("optional session is omitted from output when not provided", () => {
    const trace = buildTrace(validInputs());
    expect("session" in trace).toBe(false);
  });

  test("optional outcome is omitted from output when not provided", () => {
    const trace = buildTrace(validInputs());
    expect("outcome" in trace).toBe(false);
  });

  test("optional routing is omitted from output when not provided", () => {
    const trace = buildTrace(validInputs());
    expect("routing" in trace).toBe(false);
  });

  test("metadata is passed through verbatim", () => {
    const trace = buildTrace(validInputs({ metadata: { foo: "bar", nested: { a: 1 } } }));
    expect(trace.metadata).toEqual({ foo: "bar", nested: { a: 1 } });
  });

  test("collectedAt input is accepted but does not appear in output (Python parity)", () => {
    // Python emit.py does not emit a `collectedAt` field; we must stay byte-
    // identical, so the parameter is forward-compat but currently unused.
    const trace = buildTrace(validInputs({ collectedAt: "2026-04-17T12:00:00Z" }));
    expect("collectedAt" in trace).toBe(false);
  });
});

describe("buildTrace — error paths (spec §4.5)", () => {
  test("throws ValidationError on unknown provider name", () => {
    expect(() => buildTrace(validInputs({ provider: "not-a-provider" }))).toThrow(ValidationError);
  });

  test("throws ValidationError on empty model string", () => {
    expect(() => buildTrace(validInputs({ model: "" }))).toThrow(ValidationError);
  });

  test("throws ValidationError on empty messages list (schema requires minItems: 1)", () => {
    expect(() => buildTrace(validInputs({ messages: [] }))).toThrow(ValidationError);
  });

  test("throws ValidationError on malformed timing (non-ISO string)", () => {
    expect(() =>
      buildTrace(
        validInputs({
          timing: {
            startedAt: "not-a-date",
            endedAt: "2026-04-17T12:00:01.000Z",
            latencyMs: 1000,
          },
        }),
      ),
    ).toThrow(ValidationError);
  });

  test("throws ValidationError on negative usage token counts", () => {
    expect(() => buildTrace(validInputs({ usage: { tokensIn: -1, tokensOut: 5 } }))).toThrow(ValidationError);
  });

  test("throws ValidationError on malformed environmentTag", () => {
    expect(() =>
      buildTrace(validInputs({ env: { environmentTag: "" as EnvironmentTag, appId: "my-app" as AppId } })),
    ).toThrow(ValidationError);
  });

  test("ValidationError carries actionable fieldErrors", () => {
    try {
      buildTrace(validInputs({ provider: "not-a-provider" }));
      expect.fail("expected ValidationError");
    } catch (err) {
      expect(err).toBeInstanceOf(ValidationError);
      const ve = err as ValidationError;
      expect(ve.fieldErrors.length).toBeGreaterThan(0);
    }
  });
});

describe("buildTrace — feedbackRefs / toolCalls / routing pass-through", () => {
  test("toolCalls array is preserved", () => {
    const toolCalls = [
      { toolName: "search", args: { q: "test" }, durationMs: 50 },
    ];
    const trace = buildTrace(validInputs({ toolCalls }));
    expect(trace.toolCalls).toEqual(toolCalls);
  });

  test("feedbackRefs array is preserved", () => {
    const feedbackRefs = [
      {
        kind: "thumbs" as const,
        submittedAt: "2026-04-17T12:00:02.000Z",
        ref: "fb-123" as import("../../../src/production-traces/contract/branded-ids.js").FeedbackRefId,
        score: 1,
      },
    ];
    const trace = buildTrace(validInputs({ feedbackRefs }));
    expect(trace.feedbackRefs).toEqual(feedbackRefs);
  });

  test("routing decision (AC-545) is preserved when provided", () => {
    const routing = {
      chosen: { provider: "openai", model: "gpt-4o-mini" },
      reason: "default" as const,
      evaluatedAt: "2026-04-17T12:00:00.500Z",
    };
    const trace = buildTrace(validInputs({ routing }));
    expect(trace.routing).toEqual(routing);
  });
});
