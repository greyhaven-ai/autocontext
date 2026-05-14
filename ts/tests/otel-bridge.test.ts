/**
 * AC-682: OpenTelemetry-compatible trace import/export.
 *
 * Bidirectional bridge between `PublicTrace` and a minimal subset of
 * OTel JSON `ResourceSpans` shape. The mapping is deliberately narrow:
 * core round-trip fields (traceId, sourceHarness, messages with
 * toolCalls, outcome) are preserved exactly; anything that can't round-
 * trip cleanly (fileReferences, redactions metadata, tool result
 * payloads) is documented in `docs/opentelemetry-bridge.md`.
 *
 * Slice 1 ships the TS side only. Python parity, OTLP protobuf wire
 * format, and ProductionTrace bridge are out of scope.
 */

import { describe, expect, it } from "vitest";

import {
  OtelResourceSpansSchema,
  otelResourceSpansToPublicTrace,
  publicTraceToOtelResourceSpans,
  type PublicTrace,
} from "../src/index.js";

const SAMPLE_TRACE: PublicTrace = {
  schemaVersion: "1.0.0",
  traceId: "trace_otel_round_trip",
  sessionId: "session_001",
  sourceHarness: "autocontext",
  collectedAt: "2026-05-14T18:00:00.000Z",
  messages: [
    {
      role: "user",
      content: "Patch foo.ts",
      timestamp: "2026-05-14T18:00:01.000Z",
    },
    {
      role: "assistant",
      content: "Trying patch.",
      timestamp: "2026-05-14T18:00:02.000Z",
      toolCalls: [
        {
          toolName: "patch",
          args: { path: "foo.ts" },
          durationMs: 120,
          error: "hunk failed",
        },
      ],
    },
  ],
  outcome: {
    score: 0.3,
    reasoning: "Broken.",
    dimensions: { correctness: 0.1, polish: 0.5 },
  },
};

describe("publicTraceToOtelResourceSpans", () => {
  it("emits a ResourceSpans payload that validates under the OTel schema", () => {
    const otel = publicTraceToOtelResourceSpans(SAMPLE_TRACE);
    const result = OtelResourceSpansSchema.safeParse(otel);
    expect(result.success).toBe(true);
  });

  it("carries traceId and source as service.name", () => {
    const otel = publicTraceToOtelResourceSpans(SAMPLE_TRACE);
    expect(otel.resource.attributes["service.name"]).toBe("autocontext");
    // Every span shares the same hex traceId.
    const flatSpans = otel.scopeSpans.flatMap((s) => s.spans);
    expect(flatSpans.length).toBeGreaterThan(0);
    for (const span of flatSpans) {
      expect(span.traceId).toBe(SAMPLE_TRACE.traceId);
    }
  });

  it("emits one root span plus one span per message", () => {
    const otel = publicTraceToOtelResourceSpans(SAMPLE_TRACE);
    const spans = otel.scopeSpans.flatMap((s) => s.spans);
    // Root span (traceId-rooted) + 2 messages = 3 spans minimum.
    expect(spans.length).toBeGreaterThanOrEqual(3);

    const rootSpans = spans.filter((s) => s.parentSpanId === undefined);
    expect(rootSpans).toHaveLength(1);

    const messageSpans = spans.filter((s) => s.name.startsWith("message:"));
    expect(messageSpans).toHaveLength(2);
    expect(messageSpans.map((s) => s.attributes["ai.role"])).toEqual(["user", "assistant"]);
  });

  it("emits tool-call spans as children of the message span", () => {
    const otel = publicTraceToOtelResourceSpans(SAMPLE_TRACE);
    const spans = otel.scopeSpans.flatMap((s) => s.spans);
    const toolSpans = spans.filter((s) => s.name.startsWith("tool:"));
    expect(toolSpans).toHaveLength(1);
    expect(toolSpans[0]?.name).toBe("tool:patch");
    expect(toolSpans[0]?.attributes["tool.name"]).toBe("patch");
    // Failed tool call is reflected in the span status.
    expect(toolSpans[0]?.status?.code).toBe("ERROR");
    expect(toolSpans[0]?.attributes["tool.error"]).toBe("hunk failed");
  });

  it("attaches outcome score/reasoning/dimensions to the root span", () => {
    const otel = publicTraceToOtelResourceSpans(SAMPLE_TRACE);
    const root = otel.scopeSpans.flatMap((s) => s.spans).find((s) => s.parentSpanId === undefined);
    expect(root).toBeDefined();
    expect(root?.attributes["ai.outcome.score"]).toBe(0.3);
    expect(root?.attributes["ai.outcome.reasoning"]).toBe("Broken.");
    expect(root?.attributes["ai.outcome.dimensions.correctness"]).toBe(0.1);
    expect(root?.attributes["ai.outcome.dimensions.polish"]).toBe(0.5);
  });
});

describe("otelResourceSpansToPublicTrace", () => {
  it("round-trips the core fields exactly", () => {
    const otel = publicTraceToOtelResourceSpans(SAMPLE_TRACE);
    const result = otelResourceSpansToPublicTrace(otel);
    if ("error" in result) {
      throw new Error(`round-trip failed: ${result.error}`);
    }
    expect(result.trace.traceId).toBe(SAMPLE_TRACE.traceId);
    expect(result.trace.sourceHarness).toBe(SAMPLE_TRACE.sourceHarness);
    expect(result.trace.collectedAt).toBe(SAMPLE_TRACE.collectedAt);
    expect(result.trace.sessionId).toBe(SAMPLE_TRACE.sessionId);
    expect(result.trace.messages).toHaveLength(SAMPLE_TRACE.messages.length);
    expect(result.trace.messages[0]?.content).toBe(SAMPLE_TRACE.messages[0]?.content);
    expect(result.trace.messages[1]?.toolCalls?.[0]?.toolName).toBe("patch");
    expect(result.trace.messages[1]?.toolCalls?.[0]?.error).toBe("hunk failed");
    expect(result.trace.outcome?.score).toBe(0.3);
    expect(result.trace.outcome?.reasoning).toBe("Broken.");
    expect(result.trace.outcome?.dimensions.correctness).toBe(0.1);
    expect(result.trace.outcome?.dimensions.polish).toBe(0.5);
  });

  it("returns an error result when the OTel input is missing service.name", () => {
    const result = otelResourceSpansToPublicTrace({
      resource: { attributes: {} },
      scopeSpans: [],
    });
    expect("error" in result).toBe(true);
  });

  it("returns an error result when there is no root span", () => {
    const otel = publicTraceToOtelResourceSpans(SAMPLE_TRACE);
    // Strip the root span deliberately.
    const stripped = {
      ...otel,
      scopeSpans: otel.scopeSpans.map((s) => ({
        ...s,
        spans: s.spans.filter((span) => span.parentSpanId !== undefined),
      })),
    };
    const result = otelResourceSpansToPublicTrace(stripped);
    expect("error" in result).toBe(true);
  });

  it("omits outcome when none of ai.outcome.* attributes are present", () => {
    const minimal: PublicTrace = {
      ...SAMPLE_TRACE,
      outcome: undefined,
    };
    const otel = publicTraceToOtelResourceSpans(minimal);
    const result = otelResourceSpansToPublicTrace(otel);
    if ("error" in result) throw new Error(result.error);
    expect(result.trace.outcome).toBeUndefined();
  });

  it("preserves zero-tool-call messages on round-trip", () => {
    const userOnly: PublicTrace = {
      ...SAMPLE_TRACE,
      messages: [SAMPLE_TRACE.messages[0]!],
    };
    const otel = publicTraceToOtelResourceSpans(userOnly);
    const result = otelResourceSpansToPublicTrace(otel);
    if ("error" in result) throw new Error(result.error);
    expect(result.trace.messages).toHaveLength(1);
    expect(result.trace.messages[0]?.toolCalls).toBeUndefined();
  });
});

describe("redaction preservation (privacy boundary)", () => {
  it("a PublicTrace with redactions[] survives round-trip via ai.redactions attribute", () => {
    const redacted: PublicTrace = {
      ...SAMPLE_TRACE,
      redactions: [{ field: "messages[0].content", reason: "pii", method: "hash" }],
    };
    const otel = publicTraceToOtelResourceSpans(redacted);
    const result = otelResourceSpansToPublicTrace(otel);
    if ("error" in result) throw new Error(result.error);
    // The redactions metadata is preserved so a downstream consumer
    // can still see that fields were redacted upstream.
    expect(result.trace.redactions).toEqual(redacted.redactions);
  });
});
