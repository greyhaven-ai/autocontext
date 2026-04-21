/**
 * instrumentClient + non-streaming proxy tests — Task 3.7.
 * Mirrors Python proxy tests (6 scenarios).
 */
import { describe, test, expect } from "vitest";
import OpenAI from "openai";
import { instrumentClient } from "../../../src/integrations/openai/wrap.js";
import { FileSink } from "../../../src/integrations/openai/sink.js";
import { mkdtempSync, rmSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { cannedChatCompletion, cannedChatCompletionWithToolCall, jsonResponse, errorResponse } from "./_helpers/fake-fetch.js";

function makeSink() {
  const dir = mkdtempSync(join(tmpdir(), "autoctx-proxy-"));
  const path = join(dir, "traces.jsonl");
  const sink = new FileSink(path);
  return {
    sink,
    path,
    dir,
    readTraces: () => {
      sink.flush();
      return readFileSync(path, "utf-8")
        .trim()
        .split("\n")
        .filter(Boolean)
        .map((l) => JSON.parse(l) as Record<string, unknown>);
    },
    cleanup: () => rmSync(dir, { recursive: true, force: true }),
  };
}

describe("instrumentClient", () => {
  test("returns wrapped client with symbol sentinel", () => {
    const { sink, cleanup } = makeSink();
    const inner = new OpenAI({ apiKey: "test-key", fetch: () => Promise.resolve(new Response()) });
    const wrapped = instrumentClient(inner, { sink, appId: "my-app" });
    expect((wrapped as unknown as Record<symbol, boolean>)[Symbol.for("autocontext.wrapped")]).toBe(true);
    cleanup();
    sink.close();
  });

  test("double-wrap throws ValueError", () => {
    const { sink, cleanup } = makeSink();
    const inner = new OpenAI({ apiKey: "test-key", fetch: () => Promise.resolve(new Response()) });
    const wrapped = instrumentClient(inner, { sink, appId: "my-app" });
    expect(() => instrumentClient(wrapped as unknown as OpenAI, { sink, appId: "my-app" }))
      .toThrow(/already wrapped/i);
    cleanup();
    sink.close();
  });

  test("missing appId throws ValueError", () => {
    const { sink, cleanup } = makeSink();
    delete process.env["AUTOCONTEXT_APP_ID"];
    const inner = new OpenAI({ apiKey: "test-key", fetch: () => Promise.resolve(new Response()) });
    expect(() => instrumentClient(inner, { sink })).toThrow(/app_id/i);
    cleanup();
    sink.close();
  });

  test("appId resolved from env var", () => {
    const { sink, cleanup } = makeSink();
    process.env["AUTOCONTEXT_APP_ID"] = "env-app-id";
    const inner = new OpenAI({ apiKey: "test-key", fetch: () => Promise.resolve(new Response()) });
    expect(() => instrumentClient(inner, { sink })).not.toThrow();
    delete process.env["AUTOCONTEXT_APP_ID"];
    cleanup();
    sink.close();
  });

  test("non-streaming chat.completions.create emits success trace", async () => {
    const { sink, readTraces, cleanup } = makeSink();
    const fakeFetch = (_url: string, _init: RequestInit) =>
      Promise.resolve(jsonResponse(cannedChatCompletion()));
    const inner = new OpenAI({ apiKey: "test-key", fetch: fakeFetch as typeof fetch });
    const client = instrumentClient(inner, { sink, appId: "test-app", environmentTag: "test" });

    const resp = await client.chat.completions.create({
      model: "gpt-4o",
      messages: [{ role: "user", content: "Hello" }],
    });
    expect(resp.choices[0]?.message.content).toBe("hello world");

    const traces = readTraces();
    expect(traces).toHaveLength(1);
    const t = traces[0]!;
    expect(t.provider).toMatchObject({ name: "openai" });
    expect((t.outcome as Record<string, unknown>).label).toBe("success");
    expect((t.usage as Record<string, unknown>).tokensIn).toBe(10);

    cleanup();
    sink.close();
  });

  test("non-streaming chat with tool calls emits correct toolCalls in trace", async () => {
    const { sink, readTraces, cleanup } = makeSink();
    const fakeFetch = (_url: string, _init: RequestInit) =>
      Promise.resolve(jsonResponse(cannedChatCompletionWithToolCall()));
    const inner = new OpenAI({ apiKey: "test-key", fetch: fakeFetch as typeof fetch });
    const client = instrumentClient(inner, { sink, appId: "test-app", environmentTag: "test" });

    await client.chat.completions.create({
      model: "gpt-4o",
      messages: [{ role: "user", content: "Get weather" }],
    });

    const traces = readTraces();
    expect(traces).toHaveLength(1);
    const toolCalls = (traces[0] as Record<string, unknown>).toolCalls as Array<Record<string, unknown>>;
    expect(toolCalls).toHaveLength(1);
    expect(toolCalls[0]!.toolName).toBe("get_weather");

    cleanup();
    sink.close();
  });

  test("error from API emits failure trace and re-throws", async () => {
    const { sink, readTraces, cleanup } = makeSink();
    const fakeFetch = (_url: string, _init: RequestInit) =>
      Promise.resolve(errorResponse(429, "Rate limit exceeded"));
    const inner = new OpenAI({ apiKey: "test-key", fetch: fakeFetch as typeof fetch, maxRetries: 0 });
    const client = instrumentClient(inner, { sink, appId: "test-app", environmentTag: "test" });

    await expect(
      client.chat.completions.create({
        model: "gpt-4o",
        messages: [{ role: "user", content: "Hello" }],
      }),
    ).rejects.toThrow();

    const traces = readTraces();
    expect(traces).toHaveLength(1);
    expect((traces[0] as Record<string, unknown>).outcome).toMatchObject({ label: "failure" });

    cleanup();
    sink.close();
  });

  test("non-instrumented attributes pass through", () => {
    const { sink, cleanup } = makeSink();
    const inner = new OpenAI({ apiKey: "test-key", fetch: () => Promise.resolve(new Response()) });
    const client = instrumentClient(inner, { sink, appId: "test-app" });
    // apiKey is accessible on baseURL or other passthrough fields
    expect((client as unknown as Record<string, unknown>).apiKey).toBe("test-key");
    cleanup();
    sink.close();
  });
});
