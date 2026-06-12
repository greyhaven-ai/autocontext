import { describe, expect, it } from "vitest";

import { createInMemoryWorkspaceEnv } from "../src/runtimes/workspace-env.js";
import { RuntimeSession } from "../src/session/runtime-session.js";
import {
  RuntimeSessionEventLog,
  type RuntimeSessionEventStore,
  RuntimeSessionEventType,
} from "../src/session/runtime-events.js";

function createEventStore(): RuntimeSessionEventStore & { close(): void } {
  const logs = new Map<string, RuntimeSessionEventLog>();
  return {
    save(log: RuntimeSessionEventLog): void {
      logs.set(log.sessionId, log);
    },
    load(sessionId: string): RuntimeSessionEventLog | null {
      return logs.get(sessionId) ?? null;
    },
    listChildren(parentSessionId: string): RuntimeSessionEventLog[] {
      return Array.from(logs.values()).filter((log) => log.parentSessionId === parentSessionId);
    },
    close(): void {},
  } as unknown as RuntimeSessionEventStore & { close(): void };
}

function last<T>(items: readonly T[]): T | undefined {
  return items[items.length - 1];
}

describe("runtime child session controls", () => {
  it("reserves child-session slots before async workspace setup", async () => {
    const workspace = createInMemoryWorkspaceEnv({ cwd: "/workspace" });
    const eventStore = createEventStore();
    const session = RuntimeSession.create({
      sessionId: "runtime-parent",
      goal: "ship auth",
      workspace,
      eventStore,
      maxConcurrentChildTasks: 1,
    });
    const startedTasks: string[] = [];

    const [first, second] = await Promise.all([
      session.runChildTask({
        taskId: "first",
        prompt: "First child",
        role: "analyst",
        handler: (input) => {
          startedTasks.push(input.taskId);
          return { text: "first complete" };
        },
      }),
      session.runChildTask({
        taskId: "second",
        prompt: "Second child",
        role: "analyst",
        handler: (input) => {
          startedTasks.push(input.taskId);
          return { text: "second complete" };
        },
      }),
    ]);

    expect(startedTasks).toEqual(["first"]);
    expect(first).toMatchObject({ taskId: "first", isError: false, text: "first complete" });
    expect(second).toMatchObject({
      taskId: "second",
      isError: true,
      error: "Maximum concurrent child sessions (1) exceeded",
    });
    const parent = eventStore.load("runtime-parent");
    const startedEvents = (parent?.events ?? []).filter(
      (event) => event.eventType === RuntimeSessionEventType.CHILD_TASK_STARTED,
    );
    expect(startedEvents.map((event) => event.payload.taskId)).toEqual(["first"]);

    eventStore.close();
  });

  it("records max-concurrent child task guardrail failures through the facade", async () => {
    const workspace = createInMemoryWorkspaceEnv({ cwd: "/workspace" });
    const eventStore = createEventStore();
    const session = RuntimeSession.create({
      sessionId: "runtime-parent",
      goal: "ship auth",
      workspace,
      eventStore,
      maxConcurrentChildTasks: 1,
    });
    session.log.append(RuntimeSessionEventType.CHILD_TASK_STARTED, {
      taskId: "active",
      childSessionId: "child-active",
      workerId: "worker-active",
    });
    session.save();
    let called = false;

    const result = await session.runChildTask({
      taskId: "queued",
      prompt: "Queue one more",
      role: "analyst",
      handler: () => {
        called = true;
        return { text: "should not run" };
      },
    });

    expect(called).toBe(false);
    expect(result).toMatchObject({
      isError: true,
      error: "Maximum concurrent child sessions (1) exceeded",
    });
    const parent = eventStore.load("runtime-parent");
    const child = eventStore.load(result.childSessionId);
    expect(last(parent?.events ?? [])?.eventType).toBe(
      RuntimeSessionEventType.CHILD_TASK_COMPLETED,
    );
    expect(last(parent?.events ?? [])?.payload).toMatchObject({
      isError: true,
      error: "Maximum concurrent child sessions (1) exceeded",
    });
    expect(child?.metadata.status).toBe("failed");
    expect(last(child?.events ?? [])?.payload.isError).toBe(true);

    eventStore.close();
  });

  it("does not overwrite child sessions canceled while their handler is in flight", async () => {
    const workspace = createInMemoryWorkspaceEnv({ cwd: "/workspace" });
    const eventStore = createEventStore();
    const session = RuntimeSession.create({
      sessionId: "runtime-parent",
      goal: "ship auth",
      workspace,
      eventStore,
    });

    const result = await session.runChildTask({
      taskId: "child",
      prompt: "Do child work",
      role: "analyst",
      handler: (input) => {
        session.cancelChildSession({
          childSessionId: input.childSessionId,
          reason: "operator requested",
        });
        return { text: "late success" };
      },
    });

    expect(result.childSessionId).toMatch(/^task:runtime-parent:child:/);
    expect(result).toMatchObject({
      isError: true,
      error: "operator requested",
      text: "",
    });
    const parent = eventStore.load("runtime-parent");
    const canceledChild = eventStore.load(result.childSessionId);
    const completions = (parent?.events ?? []).filter(
      (event) =>
        event.eventType === RuntimeSessionEventType.CHILD_TASK_COMPLETED &&
        event.payload.childSessionId === result.childSessionId,
    );
    expect(completions).toHaveLength(1);
    expect(completions[0]?.payload).toMatchObject({
      phase: "canceled",
      status: "canceled",
      error: "operator requested",
    });
    expect(canceledChild?.metadata.status).toBe("canceled");
    expect(session.coordinator.activeWorkers).toEqual([]);
    expect(JSON.stringify(canceledChild?.toJSON())).not.toContain("late success");

    eventStore.close();
  });

  it("cancels active child sessions through the facade", () => {
    const workspace = createInMemoryWorkspaceEnv({ cwd: "/workspace" });
    const eventStore = createEventStore();
    const session = RuntimeSession.create({
      sessionId: "runtime-parent",
      goal: "ship auth",
      workspace,
      eventStore,
    });
    const child = RuntimeSessionEventLog.create({
      sessionId: "task:runtime-parent:child:worker-1",
      parentSessionId: "runtime-parent",
      taskId: "child",
      workerId: "worker-1",
      metadata: { goal: "child work", status: "running" },
    });
    child.append(RuntimeSessionEventType.PROMPT_SUBMITTED, {
      prompt: "SECRET_VALUE",
      role: "analyst",
    });
    eventStore.save(child);
    session.log.append(RuntimeSessionEventType.CHILD_TASK_STARTED, {
      taskId: "child",
      childSessionId: child.sessionId,
      workerId: "worker-1",
      role: "analyst",
    });
    session.save();

    const cancellation = session.cancelChildSession({
      childSessionId: child.sessionId,
      reason: "operator requested",
    });

    expect(cancellation).toMatchObject({
      childSessionId: child.sessionId,
      status: "canceled",
      reason: "operator requested",
    });
    const parent = eventStore.load("runtime-parent");
    const canceledChild = eventStore.load(child.sessionId);
    expect(canceledChild?.metadata.status).toBe("canceled");
    expect(last(canceledChild?.events ?? [])?.eventType).toBe(
      RuntimeSessionEventType.ASSISTANT_MESSAGE,
    );
    expect(last(canceledChild?.events ?? [])?.payload).toEqual({
      text: "",
      error: "operator requested",
      isError: true,
      phase: "canceled",
      status: "canceled",
    });
    expect(last(parent?.events ?? [])?.eventType).toBe(
      RuntimeSessionEventType.CHILD_TASK_COMPLETED,
    );
    expect(last(parent?.events ?? [])?.payload).toEqual({
      taskId: "child",
      childSessionId: child.sessionId,
      workerId: "worker-1",
      result: "",
      error: "operator requested",
      isError: true,
      phase: "canceled",
      status: "canceled",
    });

    eventStore.close();
  });
});
