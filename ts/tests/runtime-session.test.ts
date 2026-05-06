import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  createInMemoryWorkspaceEnv,
  defineRuntimeCommand,
} from "../src/runtimes/workspace-env.js";
import { RuntimeSession } from "../src/session/runtime-session.js";
import {
  RuntimeSessionEventStore,
  RuntimeSessionEventType,
} from "../src/session/runtime-events.js";

function createEventStore(): RuntimeSessionEventStore {
  const dbPath = join(mkdtempSync(join(tmpdir(), "runtime-session-")), "events.db");
  return new RuntimeSessionEventStore(dbPath);
}

describe("RuntimeSession", () => {
  it("persists prompt events before the prompt handler completes", async () => {
    const workspace = createInMemoryWorkspaceEnv({ cwd: "/workspace" });
    const eventStore = createEventStore();
    const session = RuntimeSession.create({
      sessionId: "runtime-live",
      goal: "ship auth",
      workspace,
      eventStore,
    });
    let releaseHandler!: () => void;
    let promptPromise!: Promise<unknown>;
    const handlerReleased = new Promise<void>((release) => {
      releaseHandler = release;
    });
    const handlerStarted = new Promise<void>((resolve) => {
      promptPromise = session.submitPrompt({
        prompt: "Inspect auth flow",
        role: "researcher",
        handler: async () => {
          resolve();
          await handlerReleased;
          return { text: "done" };
        },
      });
    });

    await handlerStarted;

    const inFlight = eventStore.load("runtime-live");
    expect(inFlight?.events.map((event) => event.eventType)).toEqual([
      RuntimeSessionEventType.PROMPT_SUBMITTED,
    ]);

    releaseHandler();
    await promptPromise;

    const completed = eventStore.load("runtime-live");
    expect(completed?.events.map((event) => event.eventType)).toEqual([
      RuntimeSessionEventType.PROMPT_SUBMITTED,
      RuntimeSessionEventType.ASSISTANT_MESSAGE,
    ]);
    eventStore.close();
  });

  it("notifies a runtime-session event sink for each appended event", async () => {
    const workspace = createInMemoryWorkspaceEnv({ cwd: "/workspace" });
    const observed: string[] = [];
    const session = RuntimeSession.create({
      sessionId: "runtime-observed",
      goal: "ship auth",
      workspace,
      eventSink: {
        onRuntimeSessionEvent: (event) => {
          observed.push(event.eventType);
        },
      },
    });

    await session.submitPrompt({
      prompt: "Inspect auth flow",
      handler: () => ({ text: "done" }),
    });

    expect(observed).toEqual([
      RuntimeSessionEventType.PROMPT_SUBMITTED,
      RuntimeSessionEventType.ASSISTANT_MESSAGE,
    ]);
  });

  it("submits a parent prompt through a scoped workspace and persists the event log", async () => {
    const workspace = createInMemoryWorkspaceEnv({ cwd: "/workspace" });
    const eventStore = createEventStore();
    const session = RuntimeSession.create({
      sessionId: "runtime-parent",
      goal: "ship auth",
      workspace,
      eventStore,
      metadata: { project: "autocontext" },
    });

    const result = await session.submitPrompt({
      prompt: "Inspect auth flow",
      role: "researcher",
      cwd: "project",
      handler: async ({ workspace: scopedWorkspace, sessionLog, cwd }) => {
        await scopedWorkspace.writeFile("notes.md", "parent notes\n");
        sessionLog.append(RuntimeSessionEventType.SHELL_COMMAND, {
          command: "write notes",
          exitCode: 0,
          cwd,
        });
        return { text: `parent done in ${cwd}`, metadata: { phase: "root" } };
      },
    });

    expect(result).toMatchObject({
      sessionId: "runtime-parent",
      role: "researcher",
      cwd: "/workspace/project",
      text: "parent done in /workspace/project",
      isError: false,
    });
    expect(await workspace.readFile("project/notes.md")).toBe("parent notes\n");
    expect(session.log.events.map((event) => event.eventType)).toEqual([
      RuntimeSessionEventType.PROMPT_SUBMITTED,
      RuntimeSessionEventType.SHELL_COMMAND,
      RuntimeSessionEventType.ASSISTANT_MESSAGE,
    ]);

    const loaded = eventStore.load("runtime-parent");
    expect(loaded).not.toBeNull();
    expect(loaded!.metadata).toMatchObject({
      goal: "ship auth",
      project: "autocontext",
    });
    expect(loaded!.events.at(-1)?.payload).toMatchObject({
      text: "parent done in /workspace/project",
      metadata: { phase: "root" },
      cwd: "/workspace/project",
    });

    eventStore.close();
  });

  it("runs child tasks through the facade and reloads child logs by parent session", async () => {
    const workspace = createInMemoryWorkspaceEnv({ cwd: "/workspace" });
    const eventStore = createEventStore();
    const session = RuntimeSession.create({
      sessionId: "runtime-parent",
      goal: "ship auth",
      workspace,
      eventStore,
    });

    const result = await session.runChildTask({
      taskId: "task-1",
      prompt: "Summarize auth flow",
      role: "summarizer",
      cwd: "project",
      commands: [
        defineRuntimeCommand("summarize", async (args, context) => ({
          stdout: `${context.cwd}:${args.join(" ")}`,
          stderr: "",
          exitCode: 0,
        })),
      ],
      handler: async ({ workspace: childWorkspace }) => {
        const command = await childWorkspace.exec("summarize auth flow");
        return { text: command.stdout };
      },
    });

    expect(result).toMatchObject({
      taskId: "task-1",
      parentSessionId: "runtime-parent",
      cwd: "/workspace/project",
      text: "/workspace/project:auth flow",
      isError: false,
      depth: 1,
    });
    expect(session.coordinator.fanIn()).toEqual(["/workspace/project:auth flow"]);

    const loaded = RuntimeSession.load({
      sessionId: "runtime-parent",
      workspace,
      eventStore,
    });
    expect(loaded).not.toBeNull();
    expect(loaded!.goal).toBe("ship auth");
    expect(loaded!.log.events.map((event) => event.eventType)).toEqual([
      RuntimeSessionEventType.CHILD_TASK_STARTED,
      RuntimeSessionEventType.CHILD_TASK_COMPLETED,
    ]);

    const childLogs = loaded!.listChildLogs();
    expect(childLogs).toHaveLength(1);
    expect(childLogs[0].parentSessionId).toBe("runtime-parent");
    expect(childLogs[0].taskId).toBe("task-1");
    expect(childLogs[0].events.at(-1)?.payload).toMatchObject({
      text: "/workspace/project:auth flow",
    });

    eventStore.close();
  });

  it("propagates child task depth limits through the facade", async () => {
    const workspace = createInMemoryWorkspaceEnv({ cwd: "/workspace" });
    const session = RuntimeSession.create({
      sessionId: "runtime-parent",
      goal: "ship auth",
      workspace,
      depth: 1,
      maxDepth: 1,
    });
    let called = false;

    const result = await session.runChildTask({
      taskId: "task-too-deep",
      prompt: "Delegate deeper",
      role: "researcher",
      handler: () => {
        called = true;
        return { text: "should not run" };
      },
    });

    expect(called).toBe(false);
    expect(result).toMatchObject({
      isError: true,
      error: "Maximum child task depth (1) exceeded",
      depth: 2,
      maxDepth: 1,
    });
  });
});
