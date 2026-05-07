import { describe, expect, it } from "vitest";

import { RuntimeBridgeProvider } from "../src/agents/provider-bridge.js";
import type { AgentOutput, AgentRuntime } from "../src/runtimes/base.js";
import { createInMemoryWorkspaceEnv } from "../src/runtimes/workspace-env.js";
import { RuntimeSession } from "../src/session/runtime-session.js";
import { RuntimeSessionEventType } from "../src/session/runtime-events.js";

describe("RuntimeBridgeProvider session recording", () => {
  it("records provider completions through a RuntimeSession when configured", async () => {
    const runtimeCalls: Array<{ prompt: string; system?: string }> = [];
    const runtime: AgentRuntime = {
      name: "MockRuntime",
      generate: async (opts): Promise<AgentOutput> => {
        runtimeCalls.push({ prompt: opts.prompt, system: opts.system });
        return {
          text: "session-backed answer",
          model: "mock-model",
          costUsd: 0.12,
          sessionId: "mock-agent-session",
          metadata: { traceId: "trace-1" },
        };
      },
      revise: async () => ({ text: "unused" }),
    };
    const session = RuntimeSession.create({
      sessionId: "runtime-bridge-session",
      goal: "run queued task",
      workspace: createInMemoryWorkspaceEnv({ cwd: "/workspace" }),
    });
    const provider = new RuntimeBridgeProvider(runtime, "bridge-model", {
      session,
      role: "task-runner",
      cwd: "tasks",
    });

    const result = await provider.complete({
      systemPrompt: "Be precise",
      userPrompt: "Draft the answer",
      model: "requested-model",
    });

    expect(runtimeCalls).toEqual([
      {
        prompt: "Draft the answer",
        system: "Be precise",
      },
    ]);
    expect(result).toEqual({
      text: "session-backed answer",
      model: "requested-model",
      usage: {},
    });
    expect(session.log.events.map((event) => event.eventType)).toEqual([
      RuntimeSessionEventType.PROMPT_SUBMITTED,
      RuntimeSessionEventType.ASSISTANT_MESSAGE,
    ]);
    expect(session.log.events[0].payload).toMatchObject({
      prompt: "Draft the answer",
      role: "task-runner",
      cwd: "/workspace/tasks",
    });
    expect(session.log.events[1].payload).toMatchObject({
      text: "session-backed answer",
      metadata: {
        runtime: "MockRuntime",
        operation: "generate",
        runtimeSessionId: "runtime-bridge-session",
        agentRuntimeSessionId: "mock-agent-session",
        traceId: "trace-1",
      },
      role: "task-runner",
      cwd: "/workspace/tasks",
    });
  });

  it("records provider runtime failures without converting them into empty completions", async () => {
    const failure = new Error("down");
    const runtime: AgentRuntime = {
      name: "FailingRuntime",
      generate: async (): Promise<AgentOutput> => {
        throw failure;
      },
      revise: async () => ({ text: "unused" }),
    };
    const session = RuntimeSession.create({
      sessionId: "runtime-bridge-session",
      goal: "run queued task",
      workspace: createInMemoryWorkspaceEnv({ cwd: "/workspace" }),
    });
    const provider = new RuntimeBridgeProvider(runtime, "bridge-model", {
      session,
      role: "task-runner",
      cwd: "tasks",
    });

    await expect(provider.complete({
      systemPrompt: "Be precise",
      userPrompt: "Draft the answer",
      model: "requested-model",
    })).rejects.toBe(failure);

    expect(session.log.events.map((event) => event.eventType)).toEqual([
      RuntimeSessionEventType.PROMPT_SUBMITTED,
      RuntimeSessionEventType.ASSISTANT_MESSAGE,
    ]);
    expect(session.log.events[1].payload).toMatchObject({
      text: "",
      error: "down",
      isError: true,
      role: "task-runner",
      cwd: "/workspace/tasks",
    });
  });
});
