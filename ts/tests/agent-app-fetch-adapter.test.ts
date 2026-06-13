import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import type { AutoctxAgentContext, AutoctxAgentHandler } from "../src/agent-runtime/index.js";
import {
  createAgentAppFetchHandler,
  createEdgeInMemoryWorkspaceEnv,
  createStaticAgentAppCatalog,
} from "../src/control-plane/agent-app-fetch/index.js";
import {
  RuntimeSessionEventLog,
  type RuntimeSessionEventStore,
} from "../src/session/runtime-events.js";

async function jsonBody(response: Response): Promise<unknown> {
  return await response.json();
}

function request(path: string, init?: RequestInit): Request {
  return new Request(`https://agent-app.test${path}`, init);
}

describe("generic agent app Fetch adapter", () => {
  it("keeps the adapter source free of Node server, filesystem, and provider deployment imports", () => {
    const source = readFileSync(
      join(import.meta.dirname, "..", "src", "control-plane", "agent-app-fetch", "index.ts"),
      "utf-8",
    );

    expect(source).not.toContain('"node:');
    expect(source).not.toContain("'node:");
    expect(source).not.toContain("process.env");
    expect(source).not.toMatch(/wrangler|durable object namespace|cloudflare build/i);
  });

  it("serves the manifest from a static catalog without loading handler modules", async () => {
    let loadCalls = 0;
    const handler = createAgentAppFetchHandler({
      catalog: [
        {
          name: "support",
          relativePath: ".autoctx/agents/support.mjs",
          extension: ".mjs",
          triggers: { webhook: true },
          load: async () => {
            loadCalls += 1;
            return {
              name: "support",
              relativePath: ".autoctx/agents/support.mjs",
              handler: async () => ({ ok: true }),
            };
          },
        },
      ],
    });

    const response = await handler(request("/manifest"));

    expect(response.status).toBe(200);
    expect(await jsonBody(response)).toEqual({
      ok: true,
      target: "fetch",
      agents: [
        {
          name: "support",
          relativePath: ".autoctx/agents/support.mjs",
          extension: ".mjs",
          triggers: { webhook: true },
        },
      ],
    });
    expect(loadCalls).toBe(0);
  });

  it("invokes static handlers with explicit env and an in-memory workspace", async () => {
    const supportHandler: AutoctxAgentHandler<{ message: string }> = async (ctx) => {
      const existedBefore = await ctx.workspace.exists("scratch/result.txt");
      await ctx.workspace.writeFile("scratch/result.txt", ctx.payload.message);
      return {
        id: ctx.id,
        message: await ctx.workspace.readFile("scratch/result.txt"),
        cwd: ctx.workspace.cwd,
        existedBefore,
        token: ctx.env.SUPPORT_TOKEN,
        leaked: ctx.env.SECRET_TOKEN,
      };
    };
    const handler = createAgentAppFetchHandler({
      env: {
        SUPPORT_TOKEN: "explicit-token",
        SECRET_TOKEN: undefined,
      },
      catalog: createStaticAgentAppCatalog([
        {
          name: "support",
          relativePath: ".autoctx/agents/support.mjs",
          extension: ".mjs",
          triggers: { webhook: true },
          handler: supportHandler,
        },
      ]),
    });

    const response = await handler(
      request("/agents/support/invoke", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ id: "ticket-123", payload: { message: "please triage" } }),
      }),
    );

    expect(response.status).toBe(200);
    expect(await jsonBody(response)).toEqual({
      ok: true,
      agent: "support",
      id: "ticket-123",
      result: {
        id: "ticket-123",
        message: "please triage",
        cwd: "/",
        existedBefore: false,
        token: "explicit-token",
      },
    });

    const secondResponse = await handler(
      request("/agents/support/invoke", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ id: "ticket-456", payload: { message: "second request" } }),
      }),
    );

    expect(await jsonBody(secondResponse)).toMatchObject({
      ok: true,
      id: "ticket-456",
      result: {
        message: "second request",
        existedBefore: false,
      },
    });
  });

  it("keeps the edge in-memory workspace file and directory state coherent", async () => {
    const workspace = createEdgeInMemoryWorkspaceEnv();

    await workspace.writeFile("/a", "file");

    await expect(workspace.writeFile("/a/b.txt", "child")).rejects.toThrow(
      "Not a directory: /a",
    );
    await expect(workspace.readdir("/a")).rejects.toThrow("Directory not found: /a");
    await expect(workspace.mkdir("/a")).rejects.toThrow("File exists: /a");
    await expect(workspace.mkdir("/a/child", { recursive: true })).rejects.toThrow(
      "Not a directory: /a",
    );
    await expect(workspace.mkdir("/missing/child")).rejects.toThrow(
      "Parent directory not found: /missing",
    );
    await expect(workspace.exists("/missing")).resolves.toBe(false);

    await workspace.mkdir("/missing/child", { recursive: true });

    await expect(workspace.stat("/missing/child")).resolves.toMatchObject({
      isDirectory: true,
      isFile: false,
    });
  });

  it("rejects streaming request bodies as soon as the byte limit is exceeded", async () => {
    let pullCount = 0;
    let canceled = false;
    const chunk = new Uint8Array(1024 * 1024);
    const stream = new ReadableStream<Uint8Array>({
      pull(controller) {
        pullCount += 1;
        controller.enqueue(chunk);
        if (pullCount >= 5) controller.close();
      },
      cancel() {
        canceled = true;
      },
    });
    const handler = createAgentAppFetchHandler({
      maxBodyBytes: 1,
      catalog: createStaticAgentAppCatalog([
        {
          name: "support",
          relativePath: ".autoctx/agents/support.mjs",
          extension: ".mjs",
          handler: async () => ({ ok: true }),
        },
      ]),
    });

    const response = await handler(
      new Request("https://agent-app.test/agents/support/invoke", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: stream,
        duplex: "half",
      } as RequestInit & { duplex: "half" }),
    );

    expect(response.status).toBe(413);
    expect(await jsonBody(response)).toEqual({
      ok: false,
      error: {
        code: "AUTOCTX_AGENT_APP_REQUEST_TOO_LARGE",
        message: "Request body is too large",
      },
    });
    expect(canceled).toBe(true);
    expect(pullCount).toBeLessThan(5);
  });

  it("supports runtime-backed handlers through explicit runtime and event-store capabilities", async () => {
    const savedLogs = new Map<string, RuntimeSessionEventLog>();
    const store = {
      save: (log: RuntimeSessionEventLog) => {
        savedLogs.set(log.sessionId, RuntimeSessionEventLog.fromJSON(log.toJSON()));
      },
      load: (sessionId: string) => savedLogs.get(sessionId) ?? null,
      list: () => [...savedLogs.values()],
      listChildren: () => [],
      close: () => {},
    } as unknown as RuntimeSessionEventStore;
    const handler = createAgentAppFetchHandler({
      eventStore: store,
      runtime: {
        name: "edge-test-runtime",
        generate: async ({ prompt }) => ({ text: `edge:${prompt}` }),
        revise: async () => ({ text: "unused" }),
      },
      catalog: createStaticAgentAppCatalog([
        {
          name: "prompted",
          relativePath: ".autoctx/agents/prompted.mjs",
          extension: ".mjs",
          handler: async (ctx: AutoctxAgentContext<{ prompt: string }>) => {
            const runtime = await ctx.init();
            const session = await runtime.session("default");
            const reply = await session.prompt(ctx.payload.prompt);
            return { text: reply.text, sessionId: reply.sessionId };
          },
        },
      ]),
    });

    const response = await handler(
      request("/agents/prompted/invoke", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ id: "run-1", payload: { prompt: "hello" } }),
      }),
    );

    expect(response.status).toBe(200);
    expect(await jsonBody(response)).toMatchObject({
      ok: true,
      agent: "prompted",
      id: "run-1",
      result: { text: "edge:hello", sessionId: "agent:prompted:default" },
    });
    const log = savedLogs.get("agent:prompted:default");
    expect(log?.metadata).toMatchObject({
      agentName: "prompted",
      experimentalAgentRuntime: true,
    });
    expect(log?.events.map((event) => event.eventType)).toEqual([
      "prompt_submitted",
      "assistant_message",
    ]);
  });

  it("returns stable JSON error envelopes for missing agents, invalid JSON, and handler errors", async () => {
    const handler = createAgentAppFetchHandler({
      catalog: createStaticAgentAppCatalog([
        {
          name: "support",
          relativePath: ".autoctx/agents/support.mjs",
          extension: ".mjs",
          handler: async () => {
            throw new Error("handler exploded");
          },
        },
      ]),
    });

    const missing = await handler(request("/agents/missing/invoke", { method: "POST" }));
    expect(missing.status).toBe(404);
    expect(await jsonBody(missing)).toEqual({
      ok: false,
      error: {
        code: "AUTOCTX_AGENT_NOT_FOUND",
        message: "AutoContext agent not found: missing. Available: support",
      },
    });

    const invalidJson = await handler(
      request("/agents/support/invoke", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: "{not-json",
      }),
    );
    expect(invalidJson.status).toBe(400);
    expect(await jsonBody(invalidJson)).toMatchObject({
      ok: false,
      error: { code: "AUTOCTX_AGENT_APP_BAD_REQUEST" },
    });

    const exploded = await handler(
      request("/agents/support/invoke", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ payload: {} }),
      }),
    );
    expect(exploded.status).toBe(500);
    expect(await jsonBody(exploded)).toEqual({
      ok: false,
      error: {
        code: "AUTOCTX_AGENT_APP_ERROR",
        message: "handler exploded",
      },
    });
  });
});
