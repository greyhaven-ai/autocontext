import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  RuntimeSessionEventLog,
  RuntimeSessionEventStore,
  RuntimeSessionEventType,
} from "../src/session/runtime-events.js";

describe("RuntimeSessionEventLog", () => {
  it("records ordered runtime events with session lineage", () => {
    const log = RuntimeSessionEventLog.create({
      sessionId: "session-parent",
      metadata: { goal: "ship login" },
    });

    const prompt = log.append(RuntimeSessionEventType.PROMPT_SUBMITTED, {
      prompt: "inspect auth",
      role: "researcher",
    });
    const child = log.append(RuntimeSessionEventType.CHILD_TASK_STARTED, {
      taskId: "task-1",
      childSessionId: "session-child",
      workerId: "worker-1",
      role: "researcher",
      cwd: "/workspace/project",
    });

    expect(prompt.sequence).toBe(0);
    expect(child.sequence).toBe(1);
    expect(child.sessionId).toBe("session-parent");
    expect(child.payload.childSessionId).toBe("session-child");
    expect(log.events.map((event) => event.eventType)).toEqual([
      RuntimeSessionEventType.PROMPT_SUBMITTED,
      RuntimeSessionEventType.CHILD_TASK_STARTED,
    ]);
  });

  it("round-trips child task lineage through JSON", () => {
    const log = RuntimeSessionEventLog.create({
      sessionId: "child-session",
      parentSessionId: "parent-session",
      taskId: "task-1",
      workerId: "worker-1",
    });
    log.append(RuntimeSessionEventType.ASSISTANT_MESSAGE, { text: "done" });

    const restored = RuntimeSessionEventLog.fromJSON(log.toJSON());

    expect(restored.sessionId).toBe("child-session");
    expect(restored.parentSessionId).toBe("parent-session");
    expect(restored.taskId).toBe("task-1");
    expect(restored.workerId).toBe("worker-1");
    expect(restored.events[0].payload).toEqual({ text: "done" });
  });
});

describe("RuntimeSessionEventStore", () => {
  it("persists runtime events by session in sequence order", () => {
    const dbPath = join(mkdtempSync(join(tmpdir(), "runtime-events-")), "events.db");
    const store = new RuntimeSessionEventStore(dbPath);
    const log = RuntimeSessionEventLog.create({ sessionId: "session-1" });

    log.append(RuntimeSessionEventType.PROMPT_SUBMITTED, { prompt: "do it" });
    log.append(RuntimeSessionEventType.SHELL_COMMAND, {
      command: "npm test",
      exitCode: 0,
    });
    store.save(log);

    const loaded = store.load("session-1");

    expect(loaded).not.toBeNull();
    expect(loaded!.events.map((event) => event.sequence)).toEqual([0, 1]);
    expect(loaded!.events.map((event) => event.eventType)).toEqual([
      RuntimeSessionEventType.PROMPT_SUBMITTED,
      RuntimeSessionEventType.SHELL_COMMAND,
    ]);
    expect(loaded!.events[1].payload).toEqual({ command: "npm test", exitCode: 0 });

    store.close();
  });

  it("does not truncate newer events when saving a stale loaded log", () => {
    const dbPath = join(mkdtempSync(join(tmpdir(), "runtime-events-")), "events.db");
    const store = new RuntimeSessionEventStore(dbPath);
    const log = RuntimeSessionEventLog.create({ sessionId: "session-1" });
    log.append(RuntimeSessionEventType.PROMPT_SUBMITTED, { prompt: "first" });
    store.save(log);

    const stale = store.load("session-1");
    expect(stale).not.toBeNull();

    log.append(RuntimeSessionEventType.ASSISTANT_MESSAGE, { text: "second" });
    store.save(log);
    store.save(stale!);

    const loaded = store.load("session-1");

    expect(loaded?.events.map((event) => event.eventType)).toEqual([
      RuntimeSessionEventType.PROMPT_SUBMITTED,
      RuntimeSessionEventType.ASSISTANT_MESSAGE,
    ]);
    expect(loaded?.events.map((event) => event.payload)).toEqual([
      { prompt: "first" },
      { text: "second" },
    ]);

    store.close();
  });

  it("lists child task events by parent session", () => {
    const dbPath = join(mkdtempSync(join(tmpdir(), "runtime-events-")), "events.db");
    const store = new RuntimeSessionEventStore(dbPath);
    const child = RuntimeSessionEventLog.create({
      sessionId: "child-session",
      parentSessionId: "parent-session",
      taskId: "task-1",
      workerId: "worker-1",
    });
    child.append(RuntimeSessionEventType.CHILD_TASK_COMPLETED, {
      result: "researched auth",
      isError: false,
    });
    store.save(child);

    const children = store.listChildren("parent-session");

    expect(children).toHaveLength(1);
    expect(children[0].sessionId).toBe("child-session");
    expect(children[0].taskId).toBe("task-1");
    expect(children[0].events[0].payload.result).toBe("researched auth");

    store.close();
  });

  it("lists recent runtime sessions with a bounded limit", () => {
    const dbPath = join(mkdtempSync(join(tmpdir(), "runtime-events-")), "events.db");
    const store = new RuntimeSessionEventStore(dbPath);
    const older = RuntimeSessionEventLog.fromJSON({
      sessionId: "older-session",
      parentSessionId: "",
      taskId: "",
      workerId: "",
      metadata: { goal: "older goal" },
      events: [],
      createdAt: "2026-04-10T00:00:00.000Z",
      updatedAt: "2026-04-10T00:01:00.000Z",
    });
    const newer = RuntimeSessionEventLog.fromJSON({
      sessionId: "newer-session",
      parentSessionId: "",
      taskId: "",
      workerId: "",
      metadata: { goal: "newer goal" },
      events: [],
      createdAt: "2026-04-11T00:00:00.000Z",
      updatedAt: "2026-04-11T00:01:00.000Z",
    });
    store.save(older);
    store.save(newer);

    const sessions = store.list({ limit: 1 });

    expect(sessions).toHaveLength(1);
    expect(sessions[0].sessionId).toBe("newer-session");
    expect(sessions[0].metadata.goal).toBe("newer goal");

    store.close();
  });
});
