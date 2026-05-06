import { describe, expect, it, vi } from "vitest";

import {
  readRuntimeSessionById,
  readRuntimeSessionByRunId,
  readRuntimeSessionSummaries,
  summarizeRuntimeSession,
} from "../src/session/runtime-session-read-model.js";
import {
  RuntimeSessionEventLog,
  RuntimeSessionEventType,
} from "../src/session/runtime-events.js";
import {
  buildRuntimeSessionTimeline,
  readRuntimeSessionTimelineByRunId,
} from "../src/session/runtime-session-timeline.js";

function createLog(sessionId = "run:abc:runtime"): RuntimeSessionEventLog {
  return RuntimeSessionEventLog.fromJSON({
    sessionId,
    parentSessionId: "",
    taskId: "",
    workerId: "",
    metadata: {
      goal: "autoctx run support_triage",
      runId: "abc",
    },
    createdAt: "2026-04-10T00:00:00.000Z",
    updatedAt: "2026-04-10T00:00:02.000Z",
    events: [
      {
        eventId: "event-1",
        sessionId,
        sequence: 0,
        eventType: RuntimeSessionEventType.PROMPT_SUBMITTED,
        timestamp: "2026-04-10T00:00:01.000Z",
        payload: { role: "default", prompt: "Improve support replies" },
        parentSessionId: "",
        taskId: "",
        workerId: "",
      },
    ],
  });
}

describe("runtime session read model", () => {
  it("summarizes event logs without exposing full event payloads", () => {
    expect(summarizeRuntimeSession(createLog())).toEqual({
      session_id: "run:abc:runtime",
      parent_session_id: "",
      task_id: "",
      worker_id: "",
      goal: "autoctx run support_triage",
      event_count: 1,
      created_at: "2026-04-10T00:00:00.000Z",
      updated_at: "2026-04-10T00:00:02.000Z",
    });
  });

  it("reads bounded summaries through the store port", () => {
    const list = vi.fn(() => [createLog("session-1"), createLog("session-2")]);
    const load = vi.fn();

    const summaries = readRuntimeSessionSummaries({ list, load }, { limit: 2 });

    expect(list).toHaveBeenCalledWith({ limit: 2 });
    expect(load).not.toHaveBeenCalled();
    expect(summaries.map((summary) => summary.session_id)).toEqual([
      "session-1",
      "session-2",
    ]);
  });

  it("resolves run ids to the run-scoped runtime session id", () => {
    const load = vi.fn(() => createLog("run:abc:runtime"));
    const list = vi.fn();

    const log = readRuntimeSessionByRunId({ list, load }, "abc");

    expect(load).toHaveBeenCalledWith("run:abc:runtime");
    expect(log?.sessionId).toBe("run:abc:runtime");
  });

  it("reads explicit session ids without rewriting them", () => {
    const load = vi.fn(() => createLog("custom-session"));
    const list = vi.fn();

    const log = readRuntimeSessionById({ list, load }, "custom-session");

    expect(load).toHaveBeenCalledWith("custom-session");
    expect(log?.sessionId).toBe("custom-session");
  });

  it("builds an operator-facing timeline from raw runtime events", () => {
    const log = RuntimeSessionEventLog.fromJSON({
      sessionId: "run:abc:runtime",
      parentSessionId: "",
      taskId: "",
      workerId: "",
      metadata: { goal: "autoctx run support_triage", runId: "abc" },
      createdAt: "2026-04-10T00:00:00.000Z",
      updatedAt: "2026-04-10T00:00:06.000Z",
      events: [
        {
          eventId: "event-1",
          sessionId: "run:abc:runtime",
          sequence: 0,
          eventType: RuntimeSessionEventType.PROMPT_SUBMITTED,
          timestamp: "2026-04-10T00:00:01.000Z",
          payload: { role: "architect", prompt: "Improve support replies", cwd: "/workspace" },
          parentSessionId: "",
          taskId: "",
          workerId: "",
        },
        {
          eventId: "event-2",
          sessionId: "run:abc:runtime",
          sequence: 1,
          eventType: RuntimeSessionEventType.SHELL_COMMAND,
          timestamp: "2026-04-10T00:00:02.000Z",
          payload: { command: "npm test", exitCode: 0, cwd: "/workspace" },
          parentSessionId: "",
          taskId: "",
          workerId: "",
        },
        {
          eventId: "event-3",
          sessionId: "run:abc:runtime",
          sequence: 2,
          eventType: RuntimeSessionEventType.ASSISTANT_MESSAGE,
          timestamp: "2026-04-10T00:00:03.000Z",
          payload: { role: "architect", text: "Candidate prompt", cwd: "/workspace" },
          parentSessionId: "",
          taskId: "",
          workerId: "",
        },
        {
          eventId: "event-4",
          sessionId: "run:abc:runtime",
          sequence: 3,
          eventType: RuntimeSessionEventType.CHILD_TASK_STARTED,
          timestamp: "2026-04-10T00:00:04.000Z",
          payload: {
            taskId: "task-1",
            childSessionId: "task:run:abc:runtime:task-1",
            workerId: "worker-1",
            role: "analyst",
            cwd: "/workspace",
            depth: 1,
            maxDepth: 4,
          },
          parentSessionId: "",
          taskId: "",
          workerId: "",
        },
        {
          eventId: "event-5",
          sessionId: "run:abc:runtime",
          sequence: 4,
          eventType: RuntimeSessionEventType.CHILD_TASK_COMPLETED,
          timestamp: "2026-04-10T00:00:05.000Z",
          payload: {
            taskId: "task-1",
            result: "Found failing edge case",
            isError: false,
          },
          parentSessionId: "",
          taskId: "",
          workerId: "",
        },
        {
          eventId: "event-6",
          sessionId: "run:abc:runtime",
          sequence: 5,
          eventType: RuntimeSessionEventType.PROMPT_SUBMITTED,
          timestamp: "2026-04-10T00:00:06.000Z",
          payload: { role: "coach", prompt: "Review final answer" },
          parentSessionId: "",
          taskId: "",
          workerId: "",
        },
      ],
    });

    const timeline = buildRuntimeSessionTimeline(log);

    expect(timeline.summary).toMatchObject({
      session_id: "run:abc:runtime",
      event_count: 6,
    });
    expect(timeline.item_count).toBe(4);
    expect(timeline.in_flight_count).toBe(1);
    expect(timeline.error_count).toBe(0);
    expect(timeline.items).toEqual([
      expect.objectContaining({
        kind: "prompt",
        status: "completed",
        sequence_start: 0,
        sequence_end: 2,
        role: "architect",
        cwd: "/workspace",
        prompt_preview: "Improve support replies",
        response_preview: "Candidate prompt",
      }),
      expect.objectContaining({
        kind: "event",
        event_type: "shell_command",
        sequence: 1,
        title: "shell_command command=npm test exitCode=0",
      }),
      expect.objectContaining({
        kind: "child_task",
        status: "completed",
        sequence_start: 3,
        sequence_end: 4,
        task_id: "task-1",
        child_session_id: "task:run:abc:runtime:task-1",
        result_preview: "Found failing edge case",
      }),
      expect.objectContaining({
        kind: "prompt",
        status: "in_flight",
        sequence_start: 5,
        sequence_end: null,
        role: "coach",
        prompt_preview: "Review final answer",
      }),
    ]);
  });

  it("pairs concurrent role responses by request id instead of FIFO prompt order", () => {
    const log = RuntimeSessionEventLog.fromJSON({
      sessionId: "run:abc:runtime",
      parentSessionId: "",
      taskId: "",
      workerId: "",
      metadata: { goal: "autoctx run support_triage", runId: "abc" },
      createdAt: "2026-04-10T00:00:00.000Z",
      updatedAt: "2026-04-10T00:00:04.000Z",
      events: [
        {
          eventId: "event-1",
          sessionId: "run:abc:runtime",
          sequence: 0,
          eventType: RuntimeSessionEventType.PROMPT_SUBMITTED,
          timestamp: "2026-04-10T00:00:01.000Z",
          payload: {
            requestId: "analyst-request",
            role: "analyst",
            prompt: "Analyze the failure",
            cwd: "/workspace",
          },
          parentSessionId: "",
          taskId: "",
          workerId: "",
        },
        {
          eventId: "event-2",
          sessionId: "run:abc:runtime",
          sequence: 1,
          eventType: RuntimeSessionEventType.PROMPT_SUBMITTED,
          timestamp: "2026-04-10T00:00:02.000Z",
          payload: {
            requestId: "coach-request",
            role: "coach",
            prompt: "Review the patch",
            cwd: "/workspace",
          },
          parentSessionId: "",
          taskId: "",
          workerId: "",
        },
        {
          eventId: "event-3",
          sessionId: "run:abc:runtime",
          sequence: 2,
          eventType: RuntimeSessionEventType.ASSISTANT_MESSAGE,
          timestamp: "2026-04-10T00:00:03.000Z",
          payload: {
            requestId: "coach-request",
            role: "coach",
            text: "Coach response",
            cwd: "/workspace",
          },
          parentSessionId: "",
          taskId: "",
          workerId: "",
        },
        {
          eventId: "event-4",
          sessionId: "run:abc:runtime",
          sequence: 3,
          eventType: RuntimeSessionEventType.ASSISTANT_MESSAGE,
          timestamp: "2026-04-10T00:00:04.000Z",
          payload: {
            requestId: "analyst-request",
            role: "analyst",
            text: "Analyst response",
            cwd: "/workspace",
          },
          parentSessionId: "",
          taskId: "",
          workerId: "",
        },
      ],
    });

    const timeline = buildRuntimeSessionTimeline(log);

    expect(timeline.items).toEqual([
      expect.objectContaining({
        kind: "prompt",
        request_id: "analyst-request",
        role: "analyst",
        prompt_preview: "Analyze the failure",
        response_preview: "Analyst response",
        response_event_id: "event-4",
      }),
      expect.objectContaining({
        kind: "prompt",
        request_id: "coach-request",
        role: "coach",
        prompt_preview: "Review the patch",
        response_preview: "Coach response",
        response_event_id: "event-3",
      }),
    ]);
  });

  it("does not fall back to FIFO when a response carries an unmatched request id", () => {
    const log = RuntimeSessionEventLog.fromJSON({
      sessionId: "run:abc:runtime",
      parentSessionId: "",
      taskId: "",
      workerId: "",
      metadata: { goal: "autoctx run support_triage", runId: "abc" },
      createdAt: "2026-04-10T00:00:00.000Z",
      updatedAt: "2026-04-10T00:00:02.000Z",
      events: [
        {
          eventId: "event-1",
          sessionId: "run:abc:runtime",
          sequence: 0,
          eventType: RuntimeSessionEventType.PROMPT_SUBMITTED,
          timestamp: "2026-04-10T00:00:01.000Z",
          payload: {
            requestId: "prompt-request",
            role: "analyst",
            prompt: "Analyze the failure",
          },
          parentSessionId: "",
          taskId: "",
          workerId: "",
        },
        {
          eventId: "event-2",
          sessionId: "run:abc:runtime",
          sequence: 1,
          eventType: RuntimeSessionEventType.ASSISTANT_MESSAGE,
          timestamp: "2026-04-10T00:00:02.000Z",
          payload: {
            requestId: "other-request",
            role: "coach",
            text: "Unmatched response",
          },
          parentSessionId: "",
          taskId: "",
          workerId: "",
        },
      ],
    });

    const timeline = buildRuntimeSessionTimeline(log);

    expect(timeline.items).toEqual([
      expect.objectContaining({
        kind: "prompt",
        status: "in_flight",
        request_id: "prompt-request",
        response_preview: "",
      }),
      expect.objectContaining({
        kind: "event",
        event_id: "event-2",
        event_type: RuntimeSessionEventType.ASSISTANT_MESSAGE,
      }),
    ]);
  });

  it("reads runtime-session timelines by run id through the store port", () => {
    const load = vi.fn(() => createLog("run:abc:runtime"));
    const list = vi.fn();

    const timeline = readRuntimeSessionTimelineByRunId({ list, load }, "abc");

    expect(load).toHaveBeenCalledWith("run:abc:runtime");
    expect(timeline?.summary.session_id).toBe("run:abc:runtime");
  });
});
