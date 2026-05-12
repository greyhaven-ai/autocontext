import { describe, expect, it } from "vitest";

import type { AgentRuntime } from "../src/runtimes/base.js";
import { createInMemoryWorkspaceEnv } from "../src/runtimes/workspace-env.js";
import { Coordinator, CoordinatorEventType, WorkerStatus } from "../src/session/coordinator.js";
import {
  createAgentRuntimeChildTaskHandler,
  RuntimeChildTaskRunner,
} from "../src/session/runtime-child-tasks.js";
import {
  RuntimeSessionEventLog,
  RuntimeSessionEventType,
} from "../src/session/runtime-events.js";

describe("RuntimeChildTaskRunner", () => {
  it("runs a child task with coordinator state, scoped workspace, and session lineage", async () => {
    const workspace = createInMemoryWorkspaceEnv({ cwd: "/workspace" });
    const coordinator = Coordinator.create("parent-session", "ship auth");
    const parentLog = RuntimeSessionEventLog.create({ sessionId: "parent-session" });
    const runner = new RuntimeChildTaskRunner({ coordinator, parentLog, workspace });

    const result = await runner.run({
      taskId: "task-1",
      prompt: "Research auth flow",
      role: "researcher",
      cwd: "project",
      handler: async ({ workspace: childWorkspace, sessionLog, depth, maxDepth }) => {
        expect(depth).toBe(1);
        expect(maxDepth).toBe(4);
        await childWorkspace.writeFile("notes.md", "auth notes\n");
        sessionLog.append(RuntimeSessionEventType.SHELL_COMMAND, {
          command: "write notes",
          exitCode: 0,
        });
        return { text: `completed in ${childWorkspace.cwd}` };
      },
    });

    expect(result).toMatchObject({
      taskId: "task-1",
      parentSessionId: "parent-session",
      role: "researcher",
      cwd: "/workspace/project",
      text: "completed in /workspace/project",
      isError: false,
      depth: 1,
      maxDepth: 4,
    });
    expect(result.childSessionLog.parentSessionId).toBe("parent-session");
    expect(result.childSessionLog.taskId).toBe("task-1");
    expect(result.childSessionLog.workerId).toBe(result.workerId);
    expect(result.childSessionLog.metadata).toMatchObject({ depth: 1, maxDepth: 4 });
    expect(await workspace.readFile("project/notes.md")).toBe("auth notes\n");

    expect(coordinator.workers[0].status).toBe(WorkerStatus.COMPLETED);
    expect(coordinator.fanIn()).toEqual(["completed in /workspace/project"]);
    expect(coordinator.events.map((event) => event.eventType)).toContain(
      CoordinatorEventType.WORKER_STARTED,
    );
    expect(
      coordinator.events.find((event) => event.eventType === CoordinatorEventType.WORKER_STARTED)
        ?.payload,
    ).toMatchObject({
      workerId: result.workerId,
      taskId: "task-1",
      childSessionId: result.childSessionId,
      parentSessionId: "parent-session",
      role: "researcher",
      cwd: "/workspace/project",
      depth: 1,
      maxDepth: 4,
    });
    expect(
      coordinator.events.find((event) => event.eventType === CoordinatorEventType.WORKER_COMPLETED)
        ?.payload,
    ).toMatchObject({
      workerId: result.workerId,
      taskId: "task-1",
      childSessionId: result.childSessionId,
      parentSessionId: "parent-session",
      isError: false,
    });

    expect(parentLog.events.map((event) => event.eventType)).toEqual([
      RuntimeSessionEventType.CHILD_TASK_STARTED,
      RuntimeSessionEventType.CHILD_TASK_COMPLETED,
    ]);
    expect(parentLog.events[0].payload).toMatchObject({
      taskId: "task-1",
      childSessionId: result.childSessionId,
      role: "researcher",
      cwd: "/workspace/project",
      depth: 1,
      maxDepth: 4,
    });
    expect(result.childSessionLog.events.map((event) => event.eventType)).toEqual([
      RuntimeSessionEventType.PROMPT_SUBMITTED,
      RuntimeSessionEventType.SHELL_COMMAND,
      RuntimeSessionEventType.ASSISTANT_MESSAGE,
    ]);
  });

  it("adapts an AgentRuntime into a child task handler", async () => {
    const workspace = createInMemoryWorkspaceEnv({ cwd: "/workspace" });
    const coordinator = Coordinator.create("parent-session", "ship auth");
    const parentLog = RuntimeSessionEventLog.create({ sessionId: "parent-session" });
    const runner = new RuntimeChildTaskRunner({ coordinator, parentLog, workspace });
    const calls: Array<{
      prompt: string;
      system?: string;
      schema?: Record<string, unknown>;
    }> = [];
    const runtime: AgentRuntime = {
      name: "MockRuntime",
      generate: async (opts) => {
        calls.push(opts);
        return {
          text: "runtime answer",
          structured: { summary: "ok" },
          costUsd: 0.12,
          model: "mock-model",
          sessionId: "runtime-session",
          metadata: { provider: "mock" },
        };
      },
      revise: async () => ({ text: "unused" }),
    };

    const result = await runner.run({
      taskId: "task-runtime",
      prompt: "Summarize auth flow",
      role: "summarizer",
      handler: createAgentRuntimeChildTaskHandler(runtime, {
        system: "Be concise",
        schema: { type: "object" },
      }),
    });

    expect(calls).toEqual([
      {
        prompt: "Summarize auth flow",
        system: "Be concise",
        schema: { type: "object" },
      },
    ]);
    expect(result.text).toBe("runtime answer");
    expect(result.childSessionLog.events.at(-1)?.payload).toMatchObject({
      text: "runtime answer",
      metadata: {
        runtime: "MockRuntime",
        model: "mock-model",
        agentRuntimeSessionId: "runtime-session",
        costUsd: 0.12,
        structured: { summary: "ok" },
        provider: "mock",
      },
    });
  });

  it("fails delegated tasks that exceed the configured child task depth", async () => {
    const workspace = createInMemoryWorkspaceEnv({ cwd: "/workspace" });
    const coordinator = Coordinator.create("parent-session", "ship auth");
    const parentLog = RuntimeSessionEventLog.create({ sessionId: "parent-session" });
    const runner = new RuntimeChildTaskRunner({
      coordinator,
      parentLog,
      workspace,
      depth: 1,
      maxDepth: 1,
    });
    let called = false;

    const result = await runner.run({
      taskId: "task-too-deep",
      prompt: "Research nested auth flow",
      role: "researcher",
      handler: () => {
        called = true;
        return { text: "should not run" };
      },
    });

    expect(called).toBe(false);
    expect(result).toMatchObject({
      taskId: "task-too-deep",
      isError: true,
      text: "",
      error: "Maximum child task depth (1) exceeded",
      depth: 2,
      maxDepth: 1,
    });
    expect(coordinator.workers[0].status).toBe(WorkerStatus.FAILED);
    expect(
      coordinator.events.find((event) => event.eventType === CoordinatorEventType.WORKER_FAILED)
        ?.payload,
    ).toMatchObject({
      workerId: result.workerId,
      taskId: "task-too-deep",
      childSessionId: result.childSessionId,
      parentSessionId: "parent-session",
      isError: true,
      error: "Maximum child task depth (1) exceeded",
      depth: 2,
      maxDepth: 1,
    });
    expect(parentLog.events.map((event) => event.eventType)).toEqual([
      RuntimeSessionEventType.CHILD_TASK_STARTED,
      RuntimeSessionEventType.CHILD_TASK_COMPLETED,
    ]);
    expect(parentLog.events[0].payload).toMatchObject({
      taskId: "task-too-deep",
      depth: 2,
      maxDepth: 1,
    });
    expect(parentLog.events.at(-1)?.payload).toMatchObject({
      taskId: "task-too-deep",
      isError: true,
      error: "Maximum child task depth (1) exceeded",
      depth: 2,
      maxDepth: 1,
    });
    expect(result.childSessionLog.metadata).toMatchObject({ depth: 2, maxDepth: 1 });
    expect(result.childSessionLog.events.map((event) => event.eventType)).toEqual([
      RuntimeSessionEventType.PROMPT_SUBMITTED,
      RuntimeSessionEventType.ASSISTANT_MESSAGE,
    ]);
  });

  it("records failures and marks the worker failed", async () => {
    const workspace = createInMemoryWorkspaceEnv({ cwd: "/workspace" });
    const coordinator = Coordinator.create("parent-session", "ship auth");
    const parentLog = RuntimeSessionEventLog.create({ sessionId: "parent-session" });
    const runner = new RuntimeChildTaskRunner({ coordinator, parentLog, workspace });

    const result = await runner.run({
      taskId: "task-err",
      prompt: "Research auth flow",
      role: "researcher",
      handler: async () => {
        throw new Error("model unavailable");
      },
    });

    expect(result.isError).toBe(true);
    expect(result.error).toBe("model unavailable");
    expect(coordinator.workers[0].status).toBe(WorkerStatus.FAILED);
    expect(parentLog.events.at(-1)?.payload).toMatchObject({
      taskId: "task-err",
      isError: true,
      error: "model unavailable",
    });
  });
});
