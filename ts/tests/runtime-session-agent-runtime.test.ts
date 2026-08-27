import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { createInMemoryWorkspaceEnv } from "../src/runtimes/workspace-env.js";
import { DirectAPIRuntime } from "../src/runtimes/direct-api.js";
import { RuntimeSessionAgentRuntime } from "../src/runtimes/runtime-session-agent.js";
import type { AgentOutput, AgentRuntime } from "../src/runtimes/base.js";
import type { LLMProvider } from "../src/types/index.js";
import {
  EVALUATOR_ONLY_PROMPT_REDACTION,
  EVALUATOR_ONLY_RESPONSE_REDACTION,
  RuntimeSession,
} from "../src/session/runtime-session.js";
import {
  RuntimeSessionEventStore,
  RuntimeSessionEventType,
} from "../src/session/runtime-events.js";
import { EventStreamEmitter } from "../src/loop/events.js";
import { createRuntimeSessionEventStreamSink } from "../src/server/runtime-session-event-stream.js";

function createEventStore(): RuntimeSessionEventStore {
  const dbPath = join(mkdtempSync(join(tmpdir(), "runtime-session-agent-")), "events.db");
  return new RuntimeSessionEventStore(dbPath);
}

describe("RuntimeSessionAgentRuntime", () => {
  it("redacts evaluator-only prompts, responses, and metadata from every event surface", async () => {
    const dir = mkdtempSync(join(tmpdir(), "runtime-session-private-evaluator-"));
    const eventStore = new RuntimeSessionEventStore(join(dir, "events.db"));
    const eventStreamPath = join(dir, "events.ndjson");
    const emitter = new EventStreamEmitter(eventStreamPath);
    const liveEvents: unknown[] = [];
    emitter.subscribe((_event, payload, record) => {
      liveEvents.push({ payload, record });
    });
    const session = RuntimeSession.create({
      sessionId: "private-evaluator-session",
      goal: "evaluate privately",
      workspace: createInMemoryWorkspaceEnv({ cwd: "/workspace" }),
      eventStore,
      eventSink: createRuntimeSessionEventStreamSink(emitter),
    });
    const privatePrompt = "PRIVATE_EVALUATOR_PROMPT_SECRET";
    const privateResponse = "PRIVATE_EVALUATOR_RESPONSE_SECRET";
    let receivedPrompt = "";
    const privateRuntime: AgentRuntime = {
      name: "PrivateEvaluatorRuntime",
      generate: async (opts) => {
        receivedPrompt = opts.prompt;
        return {
          text: privateResponse,
          model: "private-model",
          costUsd: 0.25,
          structured: { leaked: privateResponse },
          metadata: { privateEcho: privatePrompt },
        };
      },
      revise: async () => ({ text: "unused" }),
    };
    const runtime = new RuntimeSessionAgentRuntime({
      runtime: privateRuntime,
      session,
      role: "private-evaluator",
    });

    const output = await runtime.generate({
      prompt: privatePrompt,
      promptVisibility: "evaluator_only",
    });

    expect(receivedPrompt).toBe(privatePrompt);
    expect(output.text).toBe(privateResponse);
    expect(output.structured).toEqual({ leaked: privateResponse });

    const loaded = eventStore.load("private-evaluator-session");
    const persistedDb = JSON.stringify(loaded?.events ?? []);
    const inMemory = JSON.stringify(session.log.events);
    const eventFile = readFileSync(eventStreamPath, "utf8");
    const websocketPayloads = JSON.stringify(liveEvents);
    for (const recorded of [persistedDb, inMemory, eventFile, websocketPayloads]) {
      expect(recorded).not.toContain(privatePrompt);
      expect(recorded).not.toContain(privateResponse);
      expect(recorded).toContain("evaluator_only");
      expect(recorded).toContain("contentRedacted");
      expect(recorded).toContain(EVALUATOR_ONLY_PROMPT_REDACTION);
      expect(recorded).toContain(EVALUATOR_ONLY_RESPONSE_REDACTION);
    }
    expect(session.log.events[1]?.payload.metadata).toMatchObject({
      runtime: "PrivateEvaluatorRuntime",
      operation: "generate",
      runtimeSessionId: "private-evaluator-session",
      model: "private-model",
      costUsd: 0.25,
    });
    expect(session.log.events[1]?.payload.metadata).not.toHaveProperty("structured");
    expect(session.log.events[1]?.payload.metadata).not.toHaveProperty("privateEcho");
    eventStore.close();
  });

  it("records AgentRuntime generate calls into a RuntimeSession", async () => {
    const providerCalls: Array<{
      systemPrompt: string;
      userPrompt: string;
      model?: string;
    }> = [];
    const provider: LLMProvider = {
      name: "mock-provider",
      defaultModel: () => "default-model",
      complete: async (opts) => {
        providerCalls.push(opts);
        return {
          text: "draft answer",
          model: "mock-model",
          usage: {},
          costUsd: 0.42,
        };
      },
    };
    const eventStore = createEventStore();
    const session = RuntimeSession.create({
      sessionId: "runtime-parent",
      goal: "ship auth",
      workspace: createInMemoryWorkspaceEnv({ cwd: "/workspace" }),
      eventStore,
    });
    const runtime = new RuntimeSessionAgentRuntime({
      runtime: new DirectAPIRuntime(provider, "configured-model"),
      session,
      role: "generator",
      cwd: "project",
    });

    const output = await runtime.generate({
      prompt: "Draft auth summary",
      system: "Be precise",
    });

    expect(providerCalls).toEqual([
      {
        systemPrompt: "Be precise",
        userPrompt: "Draft auth summary",
        model: "configured-model",
      },
    ]);
    expect(runtime.name).toBe("RuntimeSession(DirectAPI)");
    expect(output).toMatchObject({
      text: "draft answer",
      model: "mock-model",
      costUsd: 0.42,
      metadata: {
        runtimeSessionId: "runtime-parent",
      },
    });
    expect(session.log.events.map((event) => event.eventType)).toEqual([
      RuntimeSessionEventType.PROMPT_SUBMITTED,
      RuntimeSessionEventType.ASSISTANT_MESSAGE,
    ]);
    expect(session.log.events[0].payload).toMatchObject({
      prompt: "Draft auth summary",
      role: "generator",
      cwd: "/workspace/project",
    });
    expect(session.log.events.at(-1)?.payload).toMatchObject({
      text: "draft answer",
      metadata: {
        runtime: "DirectAPI",
        operation: "generate",
        model: "mock-model",
        costUsd: 0.42,
      },
    });

    const loaded = eventStore.load("runtime-parent");
    expect(loaded?.events.at(-1)?.payload).toMatchObject({
      text: "draft answer",
      cwd: "/workspace/project",
    });
    eventStore.close();
  });

  it("records runtime failures as session errors while preserving rejection semantics", async () => {
    let calls = 0;
    const failure = new Error("provider unavailable");
    const failingRuntime: AgentRuntime = {
      name: "FailingRuntime",
      generate: async (): Promise<AgentOutput> => {
        calls += 1;
        throw failure;
      },
      revise: async () => ({ text: "unused" }),
    };
    const session = RuntimeSession.create({
      sessionId: "runtime-parent",
      goal: "ship auth",
      workspace: createInMemoryWorkspaceEnv({ cwd: "/workspace" }),
    });
    const runtime = new RuntimeSessionAgentRuntime({
      runtime: failingRuntime,
      session,
    });

    await expect(runtime.generate({ prompt: "Draft auth summary" })).rejects.toBe(failure);

    expect(calls).toBe(1);
    expect(session.log.events.at(-1)?.payload).toMatchObject({
      text: "",
      error: "provider unavailable",
      isError: true,
    });
  });
});
