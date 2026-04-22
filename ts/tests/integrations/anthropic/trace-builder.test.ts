/**
 * trace-builder.test.ts — Tests for Anthropic trace assembly.
 * 7 tests covering usage mapping, success trace, tool calls, stop reason, failure trace, streaming.
 */
import { describe, test, expect } from "vitest";
import { ulid } from "ulid";
import { buildSuccessTrace, buildFailureTrace, finalizeStreamingTrace, buildRequestSnapshot } from "../../../src/integrations/anthropic/trace-builder.js";
import type { AccumulatedBlock } from "../../../src/integrations/anthropic/trace-builder.js";

const FAKE_TIMING = {
  startedAt: "2026-04-22T10:00:00Z",
  endedAt: "2026-04-22T10:00:01Z",
  latencyMs: 1000,
};
const FAKE_ENV = { environmentTag: "test", appId: "test-app" };
const FAKE_SOURCE = { emitter: "sdk", sdk: { name: "autocontext-ts", version: "0.0.0" } };
const FAKE_SNAPSHOT = buildRequestSnapshot({
  model: "claude-sonnet-4-5",
  messages: [{ role: "user", content: "hello" }],
  extraKwargs: {},
});

describe("trace-builder", () => {
  test("buildSuccessTrace without cache fields: tokensIn=10, tokensOut=5", () => {
    const trace = buildSuccessTrace({
      requestSnapshot: FAKE_SNAPSHOT,
      responseContent: [{ type: "text", text: "hi" }],
      responseUsage: { input_tokens: 10, output_tokens: 5 },
      responseStopReason: "end_turn",
      identity: {},
      timing: FAKE_TIMING,
      env: FAKE_ENV,
      sourceInfo: FAKE_SOURCE,
      traceId: ulid(),
    });
    const usage = trace.usage as Record<string, unknown>;
    expect(usage["tokensIn"]).toBe(10);
    expect(usage["tokensOut"]).toBe(5);
  });

  test("buildSuccessTrace with cache fields: tokensIn = input + cacheCreate + cacheRead", () => {
    const trace = buildSuccessTrace({
      requestSnapshot: FAKE_SNAPSHOT,
      responseContent: [{ type: "text", text: "cached" }],
      responseUsage: {
        input_tokens: 10,
        cache_creation_input_tokens: 20,
        cache_read_input_tokens: 30,
        output_tokens: 5,
      },
      responseStopReason: "end_turn",
      identity: {},
      timing: FAKE_TIMING,
      env: FAKE_ENV,
      sourceInfo: FAKE_SOURCE,
      traceId: ulid(),
    });
    const usage = trace.usage as Record<string, unknown>;
    expect(usage["tokensIn"]).toBe(60); // 10 + 20 + 30
    expect(usage["tokensOut"]).toBe(5);
  });

  test("buildSuccessTrace with content blocks: last message has flattened content", () => {
    const trace = buildSuccessTrace({
      requestSnapshot: FAKE_SNAPSHOT,
      responseContent: [
        { type: "text", text: "hello " },
        { type: "text", text: "world" },
      ],
      responseUsage: { input_tokens: 5, output_tokens: 3 },
      responseStopReason: "end_turn",
      identity: {},
      timing: FAKE_TIMING,
      env: FAKE_ENV,
      sourceInfo: FAKE_SOURCE,
      traceId: ulid(),
    });
    const messages = trace.messages as Array<Record<string, unknown>>;
    const lastMsg = messages[messages.length - 1]!;
    expect(lastMsg["role"]).toBe("assistant");
    expect(lastMsg["content"]).toBe("hello world");
  });

  test("buildSuccessTrace with tool_use blocks: toolCalls extracted", () => {
    const trace = buildSuccessTrace({
      requestSnapshot: FAKE_SNAPSHOT,
      responseContent: [
        { type: "text", text: "calling tool" },
        { type: "tool_use", id: "tu_1", name: "search", input: { query: "foo" } },
      ],
      responseUsage: { input_tokens: 8, output_tokens: 4 },
      responseStopReason: "tool_use",
      identity: {},
      timing: FAKE_TIMING,
      env: FAKE_ENV,
      sourceInfo: FAKE_SOURCE,
      traceId: ulid(),
    });
    const toolCalls = trace.toolCalls as Array<Record<string, unknown>>;
    expect(toolCalls).toHaveLength(1);
    expect(toolCalls[0]!["toolName"]).toBe("search");
    expect((toolCalls[0]!["args"] as Record<string, unknown>)["query"]).toBe("foo");
  });

  test("buildSuccessTrace with stop_reason: metadata.anthropicStopReason present", () => {
    const trace = buildSuccessTrace({
      requestSnapshot: FAKE_SNAPSHOT,
      responseContent: [{ type: "text", text: "done" }],
      responseUsage: { input_tokens: 3, output_tokens: 2 },
      responseStopReason: "end_turn",
      identity: {},
      timing: FAKE_TIMING,
      env: FAKE_ENV,
      sourceInfo: FAKE_SOURCE,
      traceId: ulid(),
    });
    const metadata = (trace as Record<string, unknown>)["metadata"] as Record<string, unknown> | undefined;
    expect(metadata?.["anthropicStopReason"]).toBe("end_turn");
  });

  test("buildFailureTrace with overloaded reason: outcome.error.type == overloaded", () => {
    const trace = buildFailureTrace({
      requestSnapshot: FAKE_SNAPSHOT,
      identity: {},
      timing: FAKE_TIMING,
      env: FAKE_ENV,
      sourceInfo: FAKE_SOURCE,
      traceId: ulid(),
      reasonKey: "overloaded",
      errorMessage: "Service overloaded",
      stack: null,
    });
    const outcome = trace.outcome as Record<string, unknown>;
    expect(outcome["label"]).toBe("failure");
    const error = outcome["error"] as Record<string, unknown>;
    expect(error["type"]).toBe("overloaded");
  });

  test("finalizeStreamingTrace assembles blocks correctly", () => {
    const blocks: Map<number, AccumulatedBlock> = new Map([
      [0, { type: "text", buffer: "streaming text", id: undefined, name: undefined }],
    ]);
    const trace = finalizeStreamingTrace({
      requestSnapshot: FAKE_SNAPSHOT,
      identity: {},
      timing: FAKE_TIMING,
      env: FAKE_ENV,
      sourceInfo: FAKE_SOURCE,
      traceId: ulid(),
      accumulatedContentBlocks: blocks,
      accumulatedUsage: { input_tokens: 5, output_tokens: 8 },
      accumulatedStopReason: "end_turn",
      outcome: { label: "success" },
    });
    const messages = trace.messages as Array<Record<string, unknown>>;
    const lastMsg = messages[messages.length - 1]!;
    expect(lastMsg["content"]).toBe("streaming text");
    const usage = trace.usage as Record<string, unknown>;
    expect(usage["tokensOut"]).toBe(8);
  });
});
