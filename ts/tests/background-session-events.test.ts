import { describe, expect, it } from "vitest";

import {
  buildArtifactCreatedSessionEvent,
  buildLifecycleSessionEvent,
  buildSessionStatusEvent,
  normalizeBackgroundSessionTimeline,
} from "../src/session/background-session-events.js";
import { RuntimeSessionEventLog, RuntimeSessionEventType } from "../src/session/runtime-events.js";

function createRuntimeLog(): RuntimeSessionEventLog {
  return RuntimeSessionEventLog.fromJSON({
    sessionId: "run:run-123:runtime",
    parentSessionId: "",
    taskId: "task-123",
    workerId: "worker-1",
    metadata: { goal: "autoctx solve billing dispute replies", runId: "run-123" },
    createdAt: "2026-06-01T00:00:00.000Z",
    updatedAt: "2026-06-01T00:00:40.000Z",
    events: [
      {
        eventId: "event-1",
        sessionId: "run:run-123:runtime",
        sequence: 0,
        eventType: RuntimeSessionEventType.PROMPT_SUBMITTED,
        timestamp: "2026-06-01T00:00:10.000Z",
        payload: {
          prompt: "Improve billing replies with TOKEN=SECRET_VALUE",
          requestId: "req-1",
          role: "competitor",
        },
        parentSessionId: "",
        taskId: "task-123",
        workerId: "worker-1",
      },
      {
        eventId: "event-2",
        sessionId: "run:run-123:runtime",
        sequence: 1,
        eventType: RuntimeSessionEventType.SHELL_COMMAND,
        timestamp: "2026-06-01T00:00:20.000Z",
        payload: { command: "npm test", exitCode: 0, cwd: "/workspace" },
        parentSessionId: "",
        taskId: "task-123",
        workerId: "worker-1",
      },
      {
        eventId: "event-3",
        sessionId: "run:run-123:runtime",
        sequence: 2,
        eventType: RuntimeSessionEventType.CHILD_TASK_STARTED,
        timestamp: "2026-06-01T00:00:30.000Z",
        payload: {
          taskId: "child-1",
          childSessionId: "task:run:run-123:runtime:child-1",
          role: "analyst",
        },
        parentSessionId: "",
        taskId: "task-123",
        workerId: "worker-1",
      },
      {
        eventId: "event-4",
        sessionId: "run:run-123:runtime",
        sequence: 3,
        eventType: RuntimeSessionEventType.CHILD_TASK_COMPLETED,
        timestamp: "2026-06-01T00:00:40.000Z",
        payload: { taskId: "child-1", isError: true, result: "SECRET_VALUE" },
        parentSessionId: "",
        taskId: "task-123",
        workerId: "worker-1",
      },
    ],
  });
}

describe("background session normalized events", () => {
  it("maps raw runtime-session events into sanitized normalized events", () => {
    const events = normalizeBackgroundSessionTimeline(createRuntimeLog());

    expect(events).toEqual([
      {
        event_id: "event-1",
        session_id: "run:run-123:runtime",
        sequence: 0,
        ts: "2026-06-01T00:00:10.000Z",
        event: "prompt_started",
        source_event_type: "prompt_submitted",
        status: "running",
        title: "Prompt started",
        payload_summary: { request_id: "req-1", role: "competitor" },
      },
      {
        event_id: "event-2",
        session_id: "run:run-123:runtime",
        sequence: 1,
        ts: "2026-06-01T00:00:20.000Z",
        event: "runtime_event",
        source_event_type: "shell_command",
        status: "completed",
        title: "Shell command",
        payload_summary: { command: "npm test", cwd: "/workspace", exit_code: 0 },
      },
      {
        event_id: "event-3",
        session_id: "run:run-123:runtime",
        sequence: 2,
        ts: "2026-06-01T00:00:30.000Z",
        event: "child_session_created",
        source_event_type: "child_task_started",
        status: "running",
        title: "Child session created",
        payload_summary: {
          child_session_id: "task:run:run-123:runtime:child-1",
          role: "analyst",
          task_id: "child-1",
        },
      },
      {
        event_id: "event-4",
        session_id: "run:run-123:runtime",
        sequence: 3,
        ts: "2026-06-01T00:00:40.000Z",
        event: "session_status",
        source_event_type: "child_task_completed",
        status: "failed",
        title: "Child session failed",
        payload_summary: { task_id: "child-1" },
      },
    ]);
    expect(JSON.stringify(events)).not.toContain("SECRET_VALUE");
  });

  it("includes child session events in parent timeline summaries", () => {
    const child = RuntimeSessionEventLog.fromJSON({
      sessionId: "task:run:run-123:runtime:child-1",
      parentSessionId: "run:run-123:runtime",
      taskId: "child-1",
      workerId: "worker-child",
      metadata: { goal: "Inspect failing test", status: "failed" },
      createdAt: "2026-06-01T00:00:31.000Z",
      updatedAt: "2026-06-01T00:00:36.000Z",
      events: [
        {
          eventId: "child-prompt",
          sessionId: "task:run:run-123:runtime:child-1",
          sequence: 0,
          eventType: RuntimeSessionEventType.PROMPT_SUBMITTED,
          timestamp: "2026-06-01T00:00:35.000Z",
          payload: { role: "analyst", prompt: "SECRET_VALUE" },
          parentSessionId: "run:run-123:runtime",
          taskId: "child-1",
          workerId: "worker-child",
        },
        {
          eventId: "child-answer",
          sessionId: "task:run:run-123:runtime:child-1",
          sequence: 1,
          eventType: RuntimeSessionEventType.ASSISTANT_MESSAGE,
          timestamp: "2026-06-01T00:00:36.000Z",
          payload: { role: "analyst", isError: true, error: "SECRET_VALUE" },
          parentSessionId: "run:run-123:runtime",
          taskId: "child-1",
          workerId: "worker-child",
        },
      ],
    });

    const events = normalizeBackgroundSessionTimeline(createRuntimeLog(), { childLogs: [child] });

    expect(events.map((event) => event.event_id)).toEqual([
      "event-1",
      "event-2",
      "event-3",
      "child-prompt",
      "child-answer",
      "event-4",
    ]);
    expect(events[3].payload_summary).toEqual({
      role: "analyst",
      child_session_id: "task:run:run-123:runtime:child-1",
      parent_session_id: "run:run-123:runtime",
      task_id: "child-1",
      worker_id: "worker-child",
    });
    expect(events[4].status).toBe("failed");
    expect(events[4].payload_summary).toEqual({
      role: "analyst",
      child_session_id: "task:run:run-123:runtime:child-1",
      parent_session_id: "run:run-123:runtime",
      task_id: "child-1",
      worker_id: "worker-child",
    });
    expect(JSON.stringify(events)).not.toContain("SECRET_VALUE");
  });

  it("marks failed runtime payloads from assistants and grants as failed", () => {
    const log = RuntimeSessionEventLog.fromJSON({
      sessionId: "run:failed-runtime:runtime",
      parentSessionId: "",
      taskId: "task-failed",
      workerId: "worker-1",
      metadata: { goal: "autoctx run failed grants", runId: "failed-runtime" },
      createdAt: "2026-06-01T01:00:00.000Z",
      updatedAt: "2026-06-01T01:00:04.000Z",
      events: [
        {
          eventId: "assistant-failed",
          sessionId: "run:failed-runtime:runtime",
          sequence: 0,
          eventType: RuntimeSessionEventType.ASSISTANT_MESSAGE,
          timestamp: "2026-06-01T01:00:01.000Z",
          payload: {
            requestId: "req-failed",
            role: "competitor",
            isError: true,
            error: "SECRET_VALUE",
          },
          parentSessionId: "",
          taskId: "task-failed",
          workerId: "worker-1",
        },
        {
          eventId: "assistant-canceled",
          sessionId: "run:failed-runtime:runtime",
          sequence: 1,
          eventType: RuntimeSessionEventType.ASSISTANT_MESSAGE,
          timestamp: "2026-06-01T01:00:01.500Z",
          payload: { phase: "canceled", isError: true, error: "SECRET_VALUE" },
          parentSessionId: "",
          taskId: "task-failed",
          workerId: "worker-1",
        },
        {
          eventId: "tool-failed",
          sessionId: "run:failed-runtime:runtime",
          sequence: 2,
          eventType: RuntimeSessionEventType.TOOL_CALL,
          timestamp: "2026-06-01T01:00:02.000Z",
          payload: { tool: "workspace.write", phase: "error", error: "SECRET_VALUE" },
          parentSessionId: "",
          taskId: "task-failed",
          workerId: "worker-1",
        },
        {
          eventId: "shell-failed",
          sessionId: "run:failed-runtime:runtime",
          sequence: 3,
          eventType: RuntimeSessionEventType.SHELL_COMMAND,
          timestamp: "2026-06-01T01:00:03.000Z",
          payload: {
            command: "npm test",
            cwd: "/workspace",
            phase: "error",
            error: "SECRET_VALUE",
          },
          parentSessionId: "",
          taskId: "task-failed",
          workerId: "worker-1",
        },
        {
          eventId: "child-canceled",
          sessionId: "run:failed-runtime:runtime",
          sequence: 4,
          eventType: RuntimeSessionEventType.CHILD_TASK_COMPLETED,
          timestamp: "2026-06-01T01:00:04.000Z",
          payload: { taskId: "child-1", phase: "canceled", isError: true, error: "SECRET_VALUE" },
          parentSessionId: "",
          taskId: "task-failed",
          workerId: "worker-1",
        },
      ],
    });

    const events = normalizeBackgroundSessionTimeline(log);

    expect(events.map((event) => [event.event_id, event.status, event.payload_summary])).toEqual([
      ["assistant-failed", "failed", { request_id: "req-failed", role: "competitor" }],
      ["assistant-canceled", "canceled", {}],
      ["tool-failed", "failed", { tool: "workspace.write" }],
      ["shell-failed", "failed", { command: "npm test", cwd: "/workspace" }],
      ["child-canceled", "canceled", { task_id: "child-1" }],
    ]);
    expect(JSON.stringify(events)).not.toContain("SECRET_VALUE");
  });

  it("builds artifact, lifecycle, and terminal status events from non-runtime sources", () => {
    expect(
      buildLifecycleSessionEvent({
        sessionId: "run:run-123:runtime",
        sequence: 10,
        timestamp: "2026-06-01T00:02:00.000Z",
        hook: "setup",
        phase: "started",
      }),
    ).toEqual({
      event_id: "lifecycle:run:run-123:runtime:setup:started:10",
      session_id: "run:run-123:runtime",
      sequence: 10,
      ts: "2026-06-01T00:02:00.000Z",
      event: "executor_starting",
      source_event_type: "lifecycle_hook",
      status: "running",
      title: "Lifecycle hook setup started",
      payload_summary: { hook: "setup", phase: "started" },
    });

    expect(
      buildLifecycleSessionEvent({
        sessionId: "run:run-123:runtime",
        sequence: 11,
        timestamp: "2026-06-01T00:02:10.000Z",
        hook: "start",
        phase: "completed",
      }).event,
    ).toBe("executor_ready");

    expect(
      buildArtifactCreatedSessionEvent({
        sessionId: "run:run-123:runtime",
        sequence: 12,
        timestamp: "2026-06-01T00:03:00.000Z",
        artifactId: "report-1",
        kind: "report",
        label: "Run report",
        url: "https://example.invalid/report",
      }),
    ).toEqual({
      event_id: "artifact:run:run-123:runtime:report-1:12",
      session_id: "run:run-123:runtime",
      sequence: 12,
      ts: "2026-06-01T00:03:00.000Z",
      event: "artifact_created",
      source_event_type: "artifact",
      status: "completed",
      title: "Artifact created",
      payload_summary: {
        artifact_id: "report-1",
        kind: "report",
        label: "Run report",
        url: "https://example.invalid/report",
      },
    });

    expect(
      buildSessionStatusEvent({
        sessionId: "run:run-123:runtime",
        sequence: 13,
        timestamp: "2026-06-01T00:04:00.000Z",
        status: "completed",
      }).event,
    ).toBe("session_completed");
  });
});
