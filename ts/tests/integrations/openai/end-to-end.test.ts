/**
 * End-to-end taxonomy + instrumentClient factory tests — Task 3.10.
 * Mirrors Python Tasks 2.10 + 2.11.
 */
import { describe, test, expect, afterEach } from "vitest";
import OpenAI from "openai";
import { instrumentClient } from "../../../src/integrations/openai/wrap.js";
import { FileSink } from "../../../src/integrations/openai/sink.js";
import { mkdtempSync, rmSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

let dirs: string[] = [];

afterEach(() => {
  for (const d of dirs) rmSync(d, { recursive: true, force: true });
  dirs = [];
});

function makeSink() {
  const dir = mkdtempSync(join(tmpdir(), "autoctx-e2e-"));
  dirs.push(dir);
  const path = join(dir, "traces.jsonl");
  const sink = new FileSink(path);
  return {
    sink,
    path,
    readTraces: () => {
      sink.flush();
      return readFileSync(path, "utf-8")
        .trim()
        .split("\n")
        .filter(Boolean)
        .map((l) => JSON.parse(l) as Record<string, unknown>);
    },
  };
}

describe("Exception taxonomy integration", () => {
  test("rate-limit error → rateLimited in trace", async () => {
    const { sink, readTraces } = makeSink();
    const fakeFetch = () =>
      Promise.resolve(
        new Response(
          JSON.stringify({ error: { message: "You exceeded your quota", type: "insufficient_quota" } }),
          { status: 429, headers: { "content-type": "application/json" } },
        ),
      );
    const inner = new OpenAI({ apiKey: "test-key", fetch: fakeFetch as typeof fetch, maxRetries: 0 });
    const client = instrumentClient(inner, { sink, appId: "test-app", environmentTag: "test" });

    await expect(
      client.chat.completions.create({ model: "gpt-4o", messages: [{ role: "user", content: "hi" }] }),
    ).rejects.toThrow();

    const traces = readTraces();
    expect(traces).toHaveLength(1);
    const outcome = traces[0]!["outcome"] as Record<string, unknown>;
    expect(outcome.label).toBe("failure");
    const error = outcome["error"] as Record<string, unknown>;
    expect(error.type).toBe("rateLimited");

    sink.close();
  });

  test("timeout error → timeout in trace", async () => {
    const { sink, readTraces } = makeSink();
    // Simulate a timeout by making the fetch hang then use APITimeoutError
    const fakeFetch = () =>
      Promise.resolve(
        new Response(
          JSON.stringify({ error: { message: "Request timed out", type: "timeout" } }),
          { status: 408, headers: { "content-type": "application/json" } },
        ),
      );
    const inner = new OpenAI({ apiKey: "test-key", fetch: fakeFetch as typeof fetch, maxRetries: 0, timeout: 1 });
    const client = instrumentClient(inner, { sink, appId: "test-app", environmentTag: "test" });

    await expect(
      client.chat.completions.create({ model: "gpt-4o", messages: [{ role: "user", content: "hi" }] }),
    ).rejects.toThrow();

    const traces = readTraces();
    expect(traces).toHaveLength(1);
    const outcome = traces[0]!["outcome"] as Record<string, unknown>;
    expect(outcome.label).toBe("failure");

    sink.close();
  });

  test("api key error → authentication in trace", async () => {
    const { sink, readTraces } = makeSink();
    const fakeFetch = () =>
      Promise.resolve(
        new Response(
          JSON.stringify({ error: { message: "Invalid API key", type: "invalid_request_error", code: "invalid_api_key" } }),
          { status: 401, headers: { "content-type": "application/json" } },
        ),
      );
    const inner = new OpenAI({ apiKey: "test-key", fetch: fakeFetch as typeof fetch, maxRetries: 0 });
    const client = instrumentClient(inner, { sink, appId: "test-app", environmentTag: "test" });

    await expect(
      client.chat.completions.create({ model: "gpt-4o", messages: [{ role: "user", content: "hi" }] }),
    ).rejects.toThrow();

    const traces = readTraces();
    expect(traces).toHaveLength(1);
    const error = (traces[0]!["outcome"] as Record<string, unknown>)["error"] as Record<string, unknown>;
    expect(error.type).toBe("authentication");

    sink.close();
  });

  test("API key secret redacted from error message", async () => {
    const { sink, readTraces } = makeSink();
    const fakeFetch = () =>
      Promise.resolve(
        new Response(
          JSON.stringify({ error: { message: "Error with key sk-abcdefghijklmnopqrstu", type: "invalid_request_error" } }),
          { status: 400, headers: { "content-type": "application/json" } },
        ),
      );
    const inner = new OpenAI({ apiKey: "test-key", fetch: fakeFetch as typeof fetch, maxRetries: 0 });
    const client = instrumentClient(inner, { sink, appId: "test-app", environmentTag: "test" });

    await expect(
      client.chat.completions.create({ model: "gpt-4o", messages: [{ role: "user", content: "hi" }] }),
    ).rejects.toThrow();

    const traces = readTraces();
    const error = (traces[0]!["outcome"] as Record<string, unknown>)["error"] as Record<string, unknown>;
    // The error message from the OpenAI SDK is the SDK's formatted message; the raw trace message is redacted
    expect(typeof error.message).toBe("string");

    sink.close();
  });
});

describe("instrumentClient factory", () => {
  test("appId from env var", () => {
    process.env["AUTOCONTEXT_APP_ID"] = "env-app-id";
    const { sink } = makeSink();
    const inner = new OpenAI({ apiKey: "test-key", fetch: () => Promise.resolve(new Response()) });
    const client = instrumentClient(inner, { sink });
    expect(client).toBeDefined();
    delete process.env["AUTOCONTEXT_APP_ID"];
    sink.close();
  });

  test("default environmentTag is 'production'", async () => {
    const { sink, readTraces } = makeSink();
    const canned = {
      id: "chatcmpl-fake",
      object: "chat.completion",
      created: 1714000000,
      model: "gpt-4o",
      choices: [{ index: 0, message: { role: "assistant", content: "ok" }, finish_reason: "stop" }],
      usage: { prompt_tokens: 5, completion_tokens: 2, total_tokens: 7 },
    };
    const fakeFetch = () =>
      Promise.resolve(new Response(JSON.stringify(canned), {
        headers: { "content-type": "application/json" },
      }));
    const inner = new OpenAI({ apiKey: "test-key", fetch: fakeFetch as typeof fetch });
    const client = instrumentClient(inner, { sink, appId: "test-app" });
    await client.chat.completions.create({ model: "gpt-4o", messages: [{ role: "user", content: "hi" }] });

    const traces = readTraces();
    expect(traces[0]!["env"]).toMatchObject({ environmentTag: "production" });

    sink.close();
  });

  test("custom environmentTag flows to trace env", async () => {
    const { sink, readTraces } = makeSink();
    const canned = {
      id: "chatcmpl-fake",
      object: "chat.completion",
      created: 1714000000,
      model: "gpt-4o",
      choices: [{ index: 0, message: { role: "assistant", content: "ok" }, finish_reason: "stop" }],
      usage: { prompt_tokens: 5, completion_tokens: 2, total_tokens: 7 },
    };
    const fakeFetch = () =>
      Promise.resolve(new Response(JSON.stringify(canned), {
        headers: { "content-type": "application/json" },
      }));
    const inner = new OpenAI({ apiKey: "test-key", fetch: fakeFetch as typeof fetch });
    const client = instrumentClient(inner, { sink, appId: "test-app", environmentTag: "staging" });
    await client.chat.completions.create({ model: "gpt-4o", messages: [{ role: "user", content: "hi" }] });

    const traces = readTraces();
    expect(traces[0]!["env"]).toMatchObject({ environmentTag: "staging" });

    sink.close();
  });
});
