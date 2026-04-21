/**
 * Trace-builder helpers tests — Task 3.5.
 * Mirrors Python _trace_builder.py tests.
 */
import { describe, test, expect } from "vitest";
import {
  buildRequestSnapshot,
  buildSuccessTrace,
  buildFailureTrace,
  finalizeStreamingTrace,
  normalizeMessages,
  normalizeToolCalls,
} from "../../../src/integrations/openai/trace-builder.js";

const BASE_ENV = { environmentTag: "test", appId: "test-app" };
const BASE_SOURCE = { emitter: "sdk", sdk: { name: "autocontext-ts", version: "0.0.0" } };
const BASE_TIMING = {
  startedAt: "2024-01-01T00:00:00Z",
  endedAt: "2024-01-01T00:00:01Z",
  latencyMs: 1000,
};

describe("buildRequestSnapshot", () => {
  test("packages model + messages + extras", () => {
    const snap = buildRequestSnapshot({
      model: "gpt-4o",
      messages: [{ role: "user", content: "hello" }],
      extraKwargs: { temperature: 0.7 },
    });
    expect(snap.model).toBe("gpt-4o");
    expect(snap.messages).toHaveLength(1);
    expect((snap.extra as Record<string, unknown>).temperature).toBe(0.7);
  });
});

describe("normalizeMessages", () => {
  test("injects timestamp when missing", () => {
    const msgs = normalizeMessages([{ role: "user", content: "hi" }]);
    expect(msgs[0]).toHaveProperty("timestamp");
    expect(typeof msgs[0]!.timestamp).toBe("string");
  });

  test("preserves existing timestamp", () => {
    const ts = "2024-01-01T00:00:00Z";
    const msgs = normalizeMessages([{ role: "user", content: "hi", timestamp: ts }]);
    expect(msgs[0]!.timestamp).toBe(ts);
  });
});

describe("normalizeToolCalls", () => {
  test("OpenAI tool_calls format → schema ToolCall format", () => {
    const raw = [
      {
        id: "call_1",
        type: "function",
        function: { name: "get_weather", arguments: '{"location":"NYC"}' },
      },
    ];
    const normalized = normalizeToolCalls(raw);
    expect(normalized).toHaveLength(1);
    expect(normalized![0]!.toolName).toBe("get_weather");
    expect((normalized![0]!.args as Record<string, unknown>).location).toBe("NYC");
  });

  test("already-schema-format tool calls pass through", () => {
    const raw = [{ toolName: "my_tool", args: { x: 1 } }];
    const normalized = normalizeToolCalls(raw);
    expect(normalized![0]!.toolName).toBe("my_tool");
  });

  test("null/empty returns null", () => {
    expect(normalizeToolCalls(null)).toBeNull();
    expect(normalizeToolCalls([])).toBeNull();
  });

  test("invalid JSON arguments → _raw fallback", () => {
    const raw = [
      { function: { name: "bad_fn", arguments: "not-json" } },
    ];
    const normalized = normalizeToolCalls(raw);
    expect((normalized![0]!.args as Record<string, unknown>)._raw).toBe("not-json");
  });
});

describe("buildSuccessTrace", () => {
  test("returns a valid ProductionTrace", () => {
    const snap = buildRequestSnapshot({
      model: "gpt-4o",
      messages: [{ role: "user", content: "hi" }],
      extraKwargs: {},
    });
    const trace = buildSuccessTrace({
      requestSnapshot: snap,
      responseUsage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 },
      responseToolCalls: null,
      identity: {},
      timing: BASE_TIMING,
      env: BASE_ENV,
      sourceInfo: BASE_SOURCE,
      traceId: "01HWTEST000000000000000001",
    });
    expect(trace.provider.name).toBe("openai");
    expect(trace.outcome?.label).toBe("success");
    expect(trace.usage.tokensIn).toBe(10);
    expect(trace.usage.tokensOut).toBe(5);
  });
});

describe("buildFailureTrace", () => {
  test("returns a failure trace with error", () => {
    const snap = buildRequestSnapshot({
      model: "gpt-4o",
      messages: [{ role: "user", content: "hi" }],
      extraKwargs: {},
    });
    const trace = buildFailureTrace({
      requestSnapshot: snap,
      identity: {},
      timing: BASE_TIMING,
      env: BASE_ENV,
      sourceInfo: BASE_SOURCE,
      traceId: "01HWTEST000000000000000002",
      reasonKey: "rateLimited",
      errorMessage: "Rate limit exceeded",
      stack: null,
    });
    expect(trace.outcome?.label).toBe("failure");
    expect(trace.outcome?.error?.type).toBe("rateLimited");
    expect(trace.outcome?.error?.message).toBe("Rate limit exceeded");
  });

  test("redacts API keys from error message", () => {
    const snap = buildRequestSnapshot({
      model: "gpt-4o",
      messages: [{ role: "user", content: "hi" }],
      extraKwargs: {},
    });
    const trace = buildFailureTrace({
      requestSnapshot: snap,
      identity: {},
      timing: BASE_TIMING,
      env: BASE_ENV,
      sourceInfo: BASE_SOURCE,
      traceId: "01HWTEST000000000000000003",
      reasonKey: "uncategorized",
      errorMessage: "Error with key sk-abcdefghijklmnopqrstuvwxyz in request",
      stack: null,
    });
    expect(trace.outcome?.error?.message).not.toContain("sk-abcdefghijklmnopqrstuvwxyz");
    expect(trace.outcome?.error?.message).toContain("<redacted>");
  });
});

describe("finalizeStreamingTrace", () => {
  test("builds a streaming trace with accumulated usage", () => {
    const snap = buildRequestSnapshot({
      model: "gpt-4o",
      messages: [{ role: "user", content: "hi" }],
      extraKwargs: {},
    });
    const trace = finalizeStreamingTrace({
      requestSnapshot: snap,
      identity: {},
      timing: BASE_TIMING,
      env: BASE_ENV,
      sourceInfo: BASE_SOURCE,
      traceId: "01HWTEST000000000000000004",
      accumulatedUsage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 },
      accumulatedToolCalls: null,
      outcome: { label: "success" },
    });
    expect(trace.usage.tokensIn).toBe(10);
    expect(trace.usage.tokensOut).toBe(5);
    expect(trace.outcome?.label).toBe("success");
  });
});
