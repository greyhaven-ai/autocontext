import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { createInMemoryWorkspaceEnv } from "../src/runtimes/workspace-env.js";
import { DirectAPIRuntime } from "../src/runtimes/direct-api.js";
import { RuntimeSessionAgentRuntime } from "../src/runtimes/runtime-session-agent.js";
import type { AgentOutput, AgentRuntime } from "../src/runtimes/base.js";
import type { LLMProvider } from "../src/types/index.js";
import { RuntimeSession } from "../src/session/runtime-session.js";
import {
  RuntimeSessionEventStore,
  RuntimeSessionEventType,
} from "../src/session/runtime-events.js";

function createEventStore(): RuntimeSessionEventStore {
  const dbPath = join(mkdtempSync(join(tmpdir(), "runtime-session-agent-")), "events.db");
  return new RuntimeSessionEventStore(dbPath);
}

describe("RuntimeSessionAgentRuntime", () => {
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
