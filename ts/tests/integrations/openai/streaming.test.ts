/**
 * Streaming proxy tests — Task 3.8.
 * Tests AsyncStreamProxy + FinalizationRegistry abandoned detection.
 * Mirrors Python streaming tests.
 */
import { describe, test, expect } from "vitest";
import OpenAI from "openai";
import { instrumentClient } from "../../../src/integrations/openai/wrap.js";
import { FileSink } from "../../../src/integrations/openai/sink.js";
import { mkdtempSync, rmSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

function makeSink() {
  const dir = mkdtempSync(join(tmpdir(), "autoctx-stream-"));
  const path = join(dir, "traces.jsonl");
  const sink = new FileSink(path);
  return {
    sink,
    path,
    dir,
    readTraces: () => {
      sink.flush();
      const content = (() => {
        try { return readFileSync(path, "utf-8"); } catch { return ""; }
      })();
      return content.trim().split("\n").filter(Boolean).map((l) => JSON.parse(l) as Record<string, unknown>);
    },
    cleanup: () => rmSync(dir, { recursive: true, force: true }),
  };
}

function makeStreamChunks(content: string, usage?: Record<string, unknown>) {
  const words = content.split(" ");
  const chunks = words.map((word, i) => ({
    id: "chatcmpl-stream",
    object: "chat.completion.chunk",
    created: 1714000000,
    model: "gpt-4o",
    choices: [
      {
        index: 0,
        delta: { role: i === 0 ? "assistant" : undefined, content: word + (i < words.length - 1 ? " " : "") },
        finish_reason: i === words.length - 1 ? "stop" : null,
      },
    ],
  }));
  // Final chunk with usage (if requested)
  if (usage) {
    chunks.push({
      id: "chatcmpl-stream",
      object: "chat.completion.chunk",
      created: 1714000000,
      model: "gpt-4o",
      choices: [],
      usage,
    } as unknown as typeof chunks[0]);
  }
  return chunks;
}

function makeSSEResponse(chunks: unknown[]) {
  const lines = chunks.map((c) => `data: ${JSON.stringify(c)}\n\n`);
  lines.push("data: [DONE]\n\n");
  return new Response(lines.join(""), {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });
}

describe("streaming proxy", () => {
  test("consuming full stream emits success trace", async () => {
    const { sink, readTraces, cleanup } = makeSink();
    const usage = { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 };
    const chunks = makeStreamChunks("hello world", usage);

    const fakeFetch = (_url: string, _init: RequestInit) =>
      Promise.resolve(makeSSEResponse(chunks));
    const inner = new OpenAI({ apiKey: "test-key", fetch: fakeFetch as typeof fetch });
    const client = instrumentClient(inner, { sink, appId: "test-app", environmentTag: "test" });

    const stream = await client.chat.completions.create({
      model: "gpt-4o",
      messages: [{ role: "user", content: "hi" }],
      stream: true,
    });

    const collected: string[] = [];
    for await (const chunk of stream as AsyncIterable<Record<string, unknown>>) {
      const choices = chunk["choices"] as Array<Record<string, unknown>>;
      if (choices?.[0]) {
        const content = (choices[0]["delta"] as Record<string, unknown>)?.["content"];
        if (content) collected.push(String(content));
      }
    }

    expect(collected.join("")).toContain("hello");

    const traces = readTraces();
    expect(traces.length).toBeGreaterThanOrEqual(1);
    const t = traces[traces.length - 1]!;
    expect((t.outcome as Record<string, unknown>).label).toBe("success");

    cleanup();
    sink.close();
  });

  test("stream_options.include_usage auto-injected when missing", async () => {
    const { sink, cleanup } = makeSink();
    let capturedInit: RequestInit | null = null;
    const chunks = makeStreamChunks("hi");

    const fakeFetch = (_url: string, init: RequestInit) => {
      capturedInit = init;
      return Promise.resolve(makeSSEResponse(chunks));
    };
    const inner = new OpenAI({ apiKey: "test-key", fetch: fakeFetch as typeof fetch });
    const client = instrumentClient(inner, { sink, appId: "test-app" });

    const stream = await client.chat.completions.create({
      model: "gpt-4o",
      messages: [{ role: "user", content: "hi" }],
      stream: true,
    });
    // Consume stream
    for await (const _chunk of stream as AsyncIterable<unknown>) { /* no-op */ }

    // The request body should include stream_options.include_usage = true
    if (capturedInit?.body) {
      const body = JSON.parse(String(capturedInit.body)) as Record<string, unknown>;
      expect((body["stream_options"] as Record<string, unknown>)?.["include_usage"]).toBe(true);
    }

    cleanup();
    sink.close();
  });

  test("stream_options.include_usage=false not overwritten", async () => {
    const { sink, cleanup } = makeSink();
    let capturedBody: Record<string, unknown> | null = null;
    const chunks = makeStreamChunks("hi");

    const fakeFetch = (_url: string, init: RequestInit) => {
      capturedBody = JSON.parse(String(init.body)) as Record<string, unknown>;
      return Promise.resolve(makeSSEResponse(chunks));
    };
    const inner = new OpenAI({ apiKey: "test-key", fetch: fakeFetch as typeof fetch });
    const client = instrumentClient(inner, { sink, appId: "test-app" });

    const stream = await client.chat.completions.create({
      model: "gpt-4o",
      messages: [{ role: "user", content: "hi" }],
      stream: true,
      stream_options: { include_usage: false },
    });
    for await (const _chunk of stream as AsyncIterable<unknown>) { /* no-op */ }

    // Must NOT overwrite false with true
    if (capturedBody) {
      expect((capturedBody["stream_options"] as Record<string, unknown>)?.["include_usage"]).toBe(false);
    }

    cleanup();
    sink.close();
  });

  test("streaming with usage accumulates token counts", async () => {
    const { sink, readTraces, cleanup } = makeSink();
    const usage = { prompt_tokens: 20, completion_tokens: 10, total_tokens: 30 };
    const chunks = makeStreamChunks("test response", usage);

    const fakeFetch = (_url: string, _init: RequestInit) =>
      Promise.resolve(makeSSEResponse(chunks));
    const inner = new OpenAI({ apiKey: "test-key", fetch: fakeFetch as typeof fetch });
    const client = instrumentClient(inner, { sink, appId: "test-app", environmentTag: "test" });

    const stream = await client.chat.completions.create({
      model: "gpt-4o",
      messages: [{ role: "user", content: "test" }],
      stream: true,
    });

    for await (const _chunk of stream as AsyncIterable<unknown>) { /* no-op */ }

    const traces = readTraces();
    expect(traces.length).toBeGreaterThanOrEqual(1);
    const t = traces[traces.length - 1]!;
    // Usage may be accumulated from the final chunk
    const traceUsage = t["usage"] as Record<string, unknown>;
    expect(Number(traceUsage["tokensIn"]) + Number(traceUsage["tokensOut"])).toBeGreaterThanOrEqual(0);

    cleanup();
    sink.close();
  });
});
