/**
 * responses.create coverage tests — Task 3.9.
 * Mirrors Python responses.create tests.
 */
import { describe, test, expect } from "vitest";
import OpenAI from "openai";
import { instrumentClient } from "../../../src/integrations/openai/wrap.js";
import { FileSink } from "../../../src/integrations/openai/sink.js";
import { mkdtempSync, rmSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

function makeSink() {
  const dir = mkdtempSync(join(tmpdir(), "autoctx-resp-"));
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

const CANNED_RESPONSES_RESPONSE = {
  id: "resp-fake",
  object: "realtime.response",
  model: "gpt-4o",
  status: "completed",
  output: [
    {
      id: "msg-fake",
      type: "message",
      role: "assistant",
      content: [{ type: "output_text", text: "hello" }],
    },
  ],
  usage: { input_tokens: 10, output_tokens: 5, total_tokens: 15 },
};

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

function errorResponse(status: number, message: string): Response {
  return new Response(
    JSON.stringify({ error: { message, type: "api_error", code: null } }),
    { status, headers: { "content-type": "application/json" } },
  );
}

describe("responses.create", () => {
  test("success case emits trace with correct fields", async () => {
    const { sink, readTraces, cleanup } = makeSink();
    const fakeFetch = (_url: string, _init: RequestInit) =>
      Promise.resolve(jsonResponse(CANNED_RESPONSES_RESPONSE));
    const inner = new OpenAI({ apiKey: "test-key", fetch: fakeFetch as typeof fetch });
    const client = instrumentClient(inner, { sink, appId: "test-app", environmentTag: "test" });

    // responses.create expects 'input' or 'messages'
    await (client as unknown as { responses: { create: (k: unknown) => Promise<unknown> } })
      .responses.create({ model: "gpt-4o", input: "hello" });

    const traces = readTraces();
    expect(traces).toHaveLength(1);
    const t = traces[0]!;
    expect(t.provider).toMatchObject({ name: "openai" });
    expect((t.outcome as Record<string, unknown>).label).toBe("success");
    // usage maps input_tokens → tokensIn, output_tokens → tokensOut
    const u = t.usage as Record<string, unknown>;
    expect(u.tokensIn).toBe(10);
    expect(u.tokensOut).toBe(5);

    cleanup();
    sink.close();
  });

  test("messages normalized from input string", async () => {
    const { sink, readTraces, cleanup } = makeSink();
    const fakeFetch = (_url: string, _init: RequestInit) =>
      Promise.resolve(jsonResponse(CANNED_RESPONSES_RESPONSE));
    const inner = new OpenAI({ apiKey: "test-key", fetch: fakeFetch as typeof fetch });
    const client = instrumentClient(inner, { sink, appId: "test-app", environmentTag: "test" });

    await (client as unknown as { responses: { create: (k: unknown) => Promise<unknown> } })
      .responses.create({ model: "gpt-4o", input: "my prompt" });

    const traces = readTraces();
    expect(traces).toHaveLength(1);
    const msgs = traces[0]!["messages"] as Array<Record<string, unknown>>;
    expect(msgs).toBeDefined();
    expect(msgs.length).toBeGreaterThanOrEqual(1);

    cleanup();
    sink.close();
  });

  test("failure emits failure trace", async () => {
    const { sink, readTraces, cleanup } = makeSink();
    const fakeFetch = (_url: string, _init: RequestInit) =>
      Promise.resolve(errorResponse(429, "Rate limit"));
    const inner = new OpenAI({ apiKey: "test-key", fetch: fakeFetch as typeof fetch, maxRetries: 0 });
    const client = instrumentClient(inner, { sink, appId: "test-app", environmentTag: "test" });

    await expect(
      (client as unknown as { responses: { create: (k: unknown) => Promise<unknown> } })
        .responses.create({ model: "gpt-4o", input: "hello" }),
    ).rejects.toThrow();

    const traces = readTraces();
    expect(traces).toHaveLength(1);
    expect((traces[0]!.outcome as Record<string, unknown>).label).toBe("failure");

    cleanup();
    sink.close();
  });
});
