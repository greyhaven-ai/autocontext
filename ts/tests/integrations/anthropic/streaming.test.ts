/**
 * streaming.test.ts — AnthropicStreamProxy tests.
 * 4 tests: normal streaming, tool use streaming, malformed tool input, abandoned stream.
 */
import { describe, test, expect } from "vitest";
import Anthropic from "@anthropic-ai/sdk";
import { instrumentClient } from "../../../src/integrations/anthropic/wrap.js";
import { FileSink } from "../../../src/integrations/_shared/sink.js";
import { mkdtempSync, rmSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  cannedAnthropicSseResponse,
} from "./_helpers/fake-fetch.js";

function makeSink() {
  const dir = mkdtempSync(join(tmpdir(), "autoctx-anthropic-stream-"));
  const path = join(dir, "traces.jsonl");
  const sink = new FileSink(path);
  return {
    sink,
    path,
    dir,
    readTraces: () => {
      sink.flush();
      const content = (() => {
        try {
          return readFileSync(path, "utf-8");
        } catch {
          return "";
        }
      })();
      return content
        .trim()
        .split("\n")
        .filter(Boolean)
        .map((l) => JSON.parse(l) as Record<string, unknown>);
    },
    cleanup: () => rmSync(dir, { recursive: true, force: true }),
  };
}

describe("Anthropic streaming proxy", () => {
  test("normal streaming emits success trace with text content", async () => {
    const { sink, readTraces, cleanup } = makeSink();
    const fakeFetch = (_url: string, _init: RequestInit) =>
      Promise.resolve(
        cannedAnthropicSseResponse({
          textPieces: ["hello", " world"],
          usage: { input_tokens: 5, output_tokens: 2 },
          stopReason: "end_turn",
        }),
      );
    const inner = new Anthropic({
      apiKey: "test-key",
      fetch: fakeFetch as typeof fetch,
    });
    const client = instrumentClient(inner, {
      sink,
      appId: "test-app",
      environmentTag: "test",
    });

    const stream = client.messages.create({
      model: "claude-sonnet-4-5",
      max_tokens: 100,
      messages: [{ role: "user", content: "hi" }],
      stream: true,
    });

    const collected: string[] = [];
    for await (const event of stream as AsyncIterable<Record<string, unknown>>) {
      if (event["type"] === "content_block_delta") {
        const delta = event["delta"] as Record<string, unknown>;
        if (delta["type"] === "text_delta") {
          collected.push(String(delta["text"] ?? ""));
        }
      }
    }

    expect(collected.join("")).toBe("hello world");

    const traces = readTraces();
    expect(traces.length).toBeGreaterThanOrEqual(1);
    const t = traces[traces.length - 1]!;
    expect((t.outcome as Record<string, unknown>).label).toBe("success");
    const messages = t.messages as Array<Record<string, unknown>>;
    const lastMsg = messages[messages.length - 1]!;
    expect(lastMsg["content"]).toBe("hello world");

    cleanup();
    sink.close();
  });

  test("streaming with tool use emits toolCalls in trace", async () => {
    const { sink, readTraces, cleanup } = makeSink();
    const fakeFetch = (_url: string, _init: RequestInit) =>
      Promise.resolve(
        cannedAnthropicSseResponse({
          textPieces: ["I'll search for that."],
          toolUse: {
            id: "toolu_stream_01",
            name: "web_search",
            inputJsonDeltaChunks: ['{"query":', '"streaming test"}'],
          },
          usage: { input_tokens: 10, output_tokens: 8 },
          stopReason: "tool_use",
        }),
      );
    const inner = new Anthropic({
      apiKey: "test-key",
      fetch: fakeFetch as typeof fetch,
    });
    const client = instrumentClient(inner, {
      sink,
      appId: "test-app",
      environmentTag: "test",
    });

    const stream = client.messages.create({
      model: "claude-sonnet-4-5",
      max_tokens: 100,
      messages: [{ role: "user", content: "search" }],
      stream: true,
    });

    for await (const _event of stream as AsyncIterable<unknown>) {
      // consume all events
    }

    const traces = readTraces();
    expect(traces.length).toBeGreaterThanOrEqual(1);
    const t = traces[traces.length - 1]!;
    const toolCalls = t.toolCalls as Array<Record<string, unknown>>;
    expect(toolCalls).toHaveLength(1);
    expect(toolCalls[0]!["toolName"]).toBe("web_search");
    const args = toolCalls[0]!["args"] as Record<string, unknown>;
    expect(args["query"]).toBe("streaming test");

    cleanup();
    sink.close();
  });

  test("malformed tool JSON results in _rawJsonError in args", async () => {
    const { sink, readTraces, cleanup } = makeSink();
    const fakeFetch = (_url: string, _init: RequestInit) =>
      Promise.resolve(
        cannedAnthropicSseResponse({
          textPieces: ["using tool"],
          toolUse: {
            id: "toolu_bad_01",
            name: "bad_tool",
            // malformed JSON
            inputJsonDeltaChunks: ["{invalid json"],
          },
          usage: { input_tokens: 5, output_tokens: 3 },
          stopReason: "tool_use",
        }),
      );
    const inner = new Anthropic({
      apiKey: "test-key",
      fetch: fakeFetch as typeof fetch,
    });
    const client = instrumentClient(inner, {
      sink,
      appId: "test-app",
      environmentTag: "test",
    });

    const stream = client.messages.create({
      model: "claude-sonnet-4-5",
      max_tokens: 100,
      messages: [{ role: "user", content: "bad json tool" }],
      stream: true,
    });

    for await (const _event of stream as AsyncIterable<unknown>) {
      // consume all events
    }

    const traces = readTraces();
    expect(traces.length).toBeGreaterThanOrEqual(1);
    const t = traces[traces.length - 1]!;
    const toolCalls = t.toolCalls as Array<Record<string, unknown>>;
    expect(toolCalls).toHaveLength(1);
    const args = toolCalls[0]!["args"] as Record<string, unknown>;
    // malformed JSON → _rawJsonError field
    expect(args["_rawJsonError"]).toBeDefined();

    cleanup();
    sink.close();
  });

  test("messages.stream() method also emits trace", async () => {
    const { sink, readTraces, cleanup } = makeSink();
    const fakeFetch = (_url: string, _init: RequestInit) =>
      Promise.resolve(
        cannedAnthropicSseResponse({
          textPieces: ["via stream method"],
          usage: { input_tokens: 3, output_tokens: 4 },
        }),
      );
    const inner = new Anthropic({
      apiKey: "test-key",
      fetch: fakeFetch as typeof fetch,
    });
    const client = instrumentClient(inner, {
      sink,
      appId: "test-app",
      environmentTag: "test",
    });

    // Use the .stream() method (not .create({stream:true}))
    const stream = client.messages.stream({
      model: "claude-sonnet-4-5",
      max_tokens: 100,
      messages: [{ role: "user", content: "hi" }],
    });

    for await (const _event of stream as AsyncIterable<unknown>) {
      // consume all events
    }

    const traces = readTraces();
    expect(traces.length).toBeGreaterThanOrEqual(1);
    expect((traces[traces.length - 1]!.outcome as Record<string, unknown>).label).toBe("success");

    cleanup();
    sink.close();
  });
});
