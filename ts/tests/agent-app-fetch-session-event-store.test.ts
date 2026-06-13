import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import type { AutoctxAgentContext } from "../src/agent-runtime/index.js";
import {
  createAgentAppFetchHandler,
  createInMemoryAgentAppFetchSessionEventStore,
  createStaticAgentAppCatalog,
} from "../src/control-plane/agent-app-fetch/index.js";

async function jsonBody(response: Response): Promise<unknown> {
  return await response.json();
}

function request(path: string, init?: RequestInit): Request {
  return new Request(`https://agent-app.test${path}`, init);
}

describe("agent app Fetch session event-store contract", () => {
  it("records and replays runtime-session events through an explicit edge-safe store", async () => {
    const sessionEventStore = createInMemoryAgentAppFetchSessionEventStore();
    const handler = createAgentAppFetchHandler({
      sessionEventStore,
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
        body: JSON.stringify({ id: "edge-run-1", payload: { prompt: "hello" } }),
      }),
    );

    expect(response.status).toBe(200);
    expect(await jsonBody(response)).toEqual({
      ok: true,
      agent: "prompted",
      id: "edge-run-1",
      result: {
        text: "edge:hello",
        sessionId: "agent:prompted:default",
      },
    });

    const replayed = await sessionEventStore.load("agent:prompted:default");

    expect(replayed).toMatchObject({
      sessionId: "agent:prompted:default",
      metadata: {
        agentName: "prompted",
        agentSessionKey: "default",
        experimentalAgentRuntime: true,
      },
    });
    expect(replayed?.events.map((event) => event.eventType)).toEqual([
      "prompt_submitted",
      "assistant_message",
    ]);
    expect(replayed?.events.map((event) => event.sequence)).toEqual([0, 1]);
    expect(replayed?.events[1]?.payload).toMatchObject({ text: "edge:hello" });
  });

  it("appends idempotently and replays events in per-session sequence order", async () => {
    const sessionEventStore = createInMemoryAgentAppFetchSessionEventStore();
    const event = {
      eventId: "evt-1",
      sessionId: "session-1",
      sequence: 0,
      eventType: "prompt_submitted",
      timestamp: "2026-06-13T00:00:00.000Z",
      payload: { prompt: "hello" },
      parentSessionId: "",
      taskId: "",
      workerId: "",
    };

    const first = await sessionEventStore.append({
      sessionId: "session-1",
      parentSessionId: "",
      taskId: "",
      workerId: "",
      metadata: { goal: "test" },
      createdAt: "2026-06-13T00:00:00.000Z",
      updatedAt: "2026-06-13T00:00:00.000Z",
      events: [event],
    });
    const duplicate = await sessionEventStore.append({
      sessionId: "session-1",
      parentSessionId: "",
      taskId: "",
      workerId: "",
      metadata: { goal: "test" },
      createdAt: "2026-06-13T00:00:00.000Z",
      updatedAt: "2026-06-13T00:00:00.000Z",
      events: [event],
    });

    expect(first).toEqual({ appended: 1, duplicateEventIds: [], nextSequence: 1 });
    expect(duplicate).toEqual({ appended: 0, duplicateEventIds: ["evt-1"], nextSequence: 1 });
    await expect(sessionEventStore.load("session-1")).resolves.toMatchObject({
      sessionId: "session-1",
      events: [event],
    });
  });

  it("documents edge consistency semantics without provider-specific storage imports", () => {
    const sessionEventStore = createInMemoryAgentAppFetchSessionEventStore();
    const source = readFileSync(
      join(
        import.meta.dirname,
        "..",
        "src",
        "control-plane",
        "agent-app-fetch",
        "session-event-store.ts",
      ),
      "utf-8",
    );

    expect(sessionEventStore.capabilities).toEqual({
      ordering: "per_session_sequence",
      idempotency: "event_id",
      consistency: "read_your_writes_after_append",
    });
    expect(source).not.toContain('"node:');
    expect(source).not.toContain("'node:");
    expect(source).not.toContain("better-sqlite3");
    expect(source).not.toMatch(/wrangler|cloudflare|vercel|deno deploy|durable object/i);
  });
});
