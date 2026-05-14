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
    // Every span shares the same OTel-format (32-hex) traceId. The
    // PublicTrace's own traceId is preserved as the `ai.trace.id`
    // attribute instead (verified in the dedicated ID-format suite).
    const flatSpans = otel.scopeSpans.flatMap((s) => s.spans);
    expect(flatSpans.length).toBeGreaterThan(0);
    const first = flatSpans[0]?.traceId;
    expect(first).toMatch(/^[0-9a-f]{32}$/);
    for (const span of flatSpans) {
      expect(span.traceId).toBe(first);
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

describe("OTel span-context ID format (PR #959 review)", () => {
  it("emits 32-hex-char traceIds and 16-hex-char spanIds for every span", () => {
    const otel = publicTraceToOtelResourceSpans(SAMPLE_TRACE);
    const spans = otel.scopeSpans.flatMap((s) => s.spans);
    expect(spans.length).toBeGreaterThan(0);
    for (const span of spans) {
      expect(span.traceId).toMatch(/^[0-9a-f]{32}$/);
      expect(span.spanId).toMatch(/^[0-9a-f]{16}$/);
      if (span.parentSpanId !== undefined) {
        expect(span.parentSpanId).toMatch(/^[0-9a-f]{16}$/);
      }
    }
  });

  it("preserves the PublicTrace traceId on the ai.trace.id attribute (not on otel.traceId)", () => {
    const otel = publicTraceToOtelResourceSpans(SAMPLE_TRACE);
    const root = otel.scopeSpans.flatMap((s) => s.spans).find((s) => s.parentSpanId === undefined);
    expect(root?.attributes["ai.trace.id"]).toBe(SAMPLE_TRACE.traceId);
    // The OTel-format traceId is a hex correlation handle, not the original.
    expect(root?.traceId).not.toBe(SAMPLE_TRACE.traceId);
  });

  it("derives IDs deterministically (round-trip emits identical IDs)", () => {
    const first = publicTraceToOtelResourceSpans(SAMPLE_TRACE);
    const second = publicTraceToOtelResourceSpans(SAMPLE_TRACE);
    expect(first).toEqual(second);
  });
});

describe("Malformed reverse-import input (PR #959 review)", () => {
  it("returns { error } instead of throwing when input is null", () => {
    const result = otelResourceSpansToPublicTrace(null);
    expect("error" in result).toBe(true);
  });

  it("returns { error } instead of throwing when scopeSpans is malformed", () => {
    // The reviewer's repro: `scopeSpans: [{}]` previously threw
    // "Cannot read properties of undefined" inside the bridge.
    const result = otelResourceSpansToPublicTrace({
      resource: { attributes: { "service.name": "x" } },
      scopeSpans: [{}],
    });
    expect("error" in result).toBe(true);
  });

  it("returns { error } when resource is missing entirely", () => {
    const result = otelResourceSpansToPublicTrace({ scopeSpans: [] });
    expect("error" in result).toBe(true);
  });

  it("returns { error } when input is a string", () => {
    const result = otelResourceSpansToPublicTrace("not an OTel payload");
    expect("error" in result).toBe(true);
  });
});

describe("Tool-call ordering after span reordering (PR #959 review)", () => {
  it("preserves tool-call order even when OTel sibling spans are reshuffled", () => {
    const multiTool: PublicTrace = {
      ...SAMPLE_TRACE,
      messages: [
        { role: "user", content: "x", timestamp: "2026-05-14T18:00:01.000Z" },
        {
          role: "assistant",
          content: "ok",
          timestamp: "2026-05-14T18:00:02.000Z",
          toolCalls: [
            { toolName: "first", args: {} },
            { toolName: "second", args: {} },
            { toolName: "third", args: {} },
          ],
        },
      ],
    };
    const otel = publicTraceToOtelResourceSpans(multiTool);

    // Reverse the tool spans inside the scopeSpans array to simulate what
    // an OTel store might do (no guaranteed sibling-span order).
    const shuffled: typeof otel = {
      ...otel,
      scopeSpans: otel.scopeSpans.map((scope) => {
        const toolSpans = scope.spans.filter((s) => s.name.startsWith("tool:"));
        const otherSpans = scope.spans.filter((s) => !s.name.startsWith("tool:"));
        return { ...scope, spans: [...otherSpans, ...toolSpans.reverse()] };
      }),
    };

    const result = otelResourceSpansToPublicTrace(shuffled);
    if ("error" in result) throw new Error(result.error);
    const tools = result.trace.messages[1]?.toolCalls?.map((c) => c.toolName);
    expect(tools).toEqual(["first", "second", "third"]);
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
