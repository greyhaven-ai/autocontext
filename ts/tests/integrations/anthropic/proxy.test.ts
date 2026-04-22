/**
 * proxy.test.ts — instrumentClient + non-streaming proxy tests for Anthropic.
 * Mirrors openai/proxy.test.ts (7 tests).
 */
import { describe, test, expect } from "vitest";
import Anthropic from "@anthropic-ai/sdk";
import { instrumentClient } from "../../../src/integrations/anthropic/wrap.js";
import { FileSink } from "../../../src/integrations/_shared/sink.js";
import { mkdtempSync, rmSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  jsonResponse,
  errorResponse,
  cannedMessagesResponse,
  cannedMessagesResponseWithToolCall,
} from "./_helpers/fake-fetch.js";

function makeSink() {
  const dir = mkdtempSync(join(tmpdir(), "autoctx-anthropic-proxy-"));
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

describe("instrumentClient (Anthropic)", () => {
  test("returns wrapped client with symbol sentinel", () => {
    const { sink, cleanup } = makeSink();
    const inner = new Anthropic({
      apiKey: "test-key",
      fetch: () => Promise.resolve(new Response()),
    });
    const wrapped = instrumentClient(inner, { sink, appId: "my-app" });
    expect(
      (wrapped as unknown as Record<symbol, boolean>)[Symbol.for("autocontext.wrapped")],
    ).toBe(true);
    cleanup();
    sink.close();
  });

  test("double-wrap throws", () => {
    const { sink, cleanup } = makeSink();
    const inner = new Anthropic({
      apiKey: "test-key",
      fetch: () => Promise.resolve(new Response()),
    });
    const wrapped = instrumentClient(inner, { sink, appId: "my-app" });
    expect(() =>
      instrumentClient(wrapped as unknown as Anthropic, { sink, appId: "my-app" }),
    ).toThrow(/already wrapped/i);
    cleanup();
    sink.close();
  });

  test("missing appId throws", () => {
    const { sink, cleanup } = makeSink();
    delete process.env["AUTOCONTEXT_APP_ID"];
    const inner = new Anthropic({
      apiKey: "test-key",
      fetch: () => Promise.resolve(new Response()),
    });
    expect(() => instrumentClient(inner, { sink })).toThrow(/app_id/i);
    cleanup();
    sink.close();
  });

  test("appId from env var", () => {
    const { sink, cleanup } = makeSink();
    process.env["AUTOCONTEXT_APP_ID"] = "env-app-id";
    const inner = new Anthropic({
      apiKey: "test-key",
      fetch: () => Promise.resolve(new Response()),
    });
    expect(() => instrumentClient(inner, { sink })).not.toThrow();
    delete process.env["AUTOCONTEXT_APP_ID"];
    cleanup();
    sink.close();
  });

  test("messages.create() non-streaming emits success trace", async () => {
    const { sink, readTraces, cleanup } = makeSink();
    const fakeFetch = (_url: string, _init: RequestInit) =>
      Promise.resolve(jsonResponse(cannedMessagesResponse()));
    const inner = new Anthropic({
      apiKey: "test-key",
      fetch: fakeFetch as typeof fetch,
    });
    const client = instrumentClient(inner, {
      sink,
      appId: "test-app",
      environmentTag: "test",
    });

    const resp = await client.messages.create({
      model: "claude-sonnet-4-5",
      max_tokens: 100,
      messages: [{ role: "user", content: "hello" }],
    });
    expect(resp.content[0]?.type).toBe("text");

    const traces = readTraces();
    expect(traces).toHaveLength(1);
    const t = traces[0]!;
    expect(t.provider).toMatchObject({ name: "anthropic" });
    expect((t.outcome as Record<string, unknown>).label).toBe("success");
    expect((t.usage as Record<string, unknown>).tokensIn).toBe(10);

    cleanup();
    sink.close();
  });

  test("messages.create() with tool_use in response -> toolCalls in trace", async () => {
    const { sink, readTraces, cleanup } = makeSink();
    const fakeFetch = (_url: string, _init: RequestInit) =>
      Promise.resolve(jsonResponse(cannedMessagesResponseWithToolCall()));
    const inner = new Anthropic({
      apiKey: "test-key",
      fetch: fakeFetch as typeof fetch,
    });
    const client = instrumentClient(inner, {
      sink,
      appId: "test-app",
      environmentTag: "test",
    });

    await client.messages.create({
      model: "claude-sonnet-4-5",
      max_tokens: 100,
      messages: [{ role: "user", content: "use a tool" }],
    });

    const traces = readTraces();
    expect(traces).toHaveLength(1);
    const toolCalls = (traces[0] as Record<string, unknown>).toolCalls as Array<
      Record<string, unknown>
    >;
    expect(toolCalls).toHaveLength(1);
    expect(toolCalls[0]!.toolName).toBe("get_weather");

    cleanup();
    sink.close();
  });

  test("API error emits failure trace and re-throws", async () => {
    const { sink, readTraces, cleanup } = makeSink();
    const fakeFetch = (_url: string, _init: RequestInit) =>
      Promise.resolve(errorResponse(529, "Service overloaded", "overloaded_error"));
    const inner = new Anthropic({
      apiKey: "test-key",
      fetch: fakeFetch as typeof fetch,
      maxRetries: 0,
    });
    const client = instrumentClient(inner, {
      sink,
      appId: "test-app",
      environmentTag: "test",
    });

    await expect(
      client.messages.create({
        model: "claude-sonnet-4-5",
        max_tokens: 100,
        messages: [{ role: "user", content: "hello" }],
      }),
    ).rejects.toThrow();

    const traces = readTraces();
    expect(traces).toHaveLength(1);
    expect((traces[0] as Record<string, unknown>).outcome).toMatchObject({
      label: "failure",
    });

    cleanup();
    sink.close();
  });
});
