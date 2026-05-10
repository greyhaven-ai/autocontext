import { describe, expect, it } from "vitest";

import {
  runtimeSessionLogToRunTrace,
} from "../src/analytics/runtime-session-run-trace.js";
import {
  RuntimeSessionEventLog,
  RuntimeSessionEventType,
} from "../src/session/runtime-events.js";

describe("runtime-session to RunTrace adapter", () => {
  it("is exported from the package entrypoint", async () => {
    const mod = await import("../src/index.js");
    expect(typeof mod.runtimeSessionLogToRunTrace).toBe("function");
  });

  it("maps selected runtime-session events without leaking raw observability metadata", () => {
    const parentLog = RuntimeSessionEventLog.fromJSON({
      sessionId: "run:run-1:runtime",
      parentSessionId: "",
      taskId: "",
      workerId: "",
      metadata: {
        runId: "run-1",
        scenarioName: "grid_ctf",
        secret: "do-not-export",
      },
      createdAt: "2026-05-10T10:00:00.000Z",
      updatedAt: "2026-05-10T10:00:05.000Z",
      events: [
        {
          eventId: "prompt-1",
          sessionId: "run:run-1:runtime",
          sequence: 0,
          eventType: RuntimeSessionEventType.PROMPT_SUBMITTED,
          timestamp: "2026-05-10T10:00:00.000Z",
          parentSessionId: "",
          taskId: "",
          workerId: "",
          payload: {
            requestId: "req-1",
            role: "analyst",
            cwd: "/workspace",
            prompt: "secret prompt text",
          },
        },
        {
          eventId: "shell-1",
          sessionId: "run:run-1:runtime",
          sequence: 1,
          eventType: RuntimeSessionEventType.SHELL_COMMAND,
          timestamp: "2026-05-10T10:00:01.000Z",
          parentSessionId: "",
          taskId: "",
          workerId: "",
          payload: {
            requestId: "req-1",
            promptEventId: "prompt-1",
            commandName: "verify",
            phase: "end",
            cwd: "/workspace",
            exitCode: 0,
            argsSummary: "verify --quick",
            stdout: "do-not-export",
          },
        },
        {
          eventId: "child-start",
          sessionId: "run:run-1:runtime",
          sequence: 2,
          eventType: RuntimeSessionEventType.CHILD_TASK_STARTED,
          timestamp: "2026-05-10T10:00:02.000Z",
          parentSessionId: "",
          taskId: "",
          workerId: "",
          payload: {
            taskId: "retry",
            childSessionId: "task:run:run-1:runtime:retry:w-1",
            workerId: "w-1",
            role: "coach",
            cwd: "/workspace",
            depth: 1,
          },
        },
        {
          eventId: "child-done",
          sessionId: "run:run-1:runtime",
          sequence: 3,
          eventType: RuntimeSessionEventType.CHILD_TASK_COMPLETED,
          timestamp: "2026-05-10T10:00:04.000Z",
          parentSessionId: "",
          taskId: "",
          workerId: "",
          payload: {
            taskId: "retry",
            childSessionId: "task:run:run-1:runtime:retry:w-1",
            workerId: "w-1",
            role: "coach",
            result: "do-not-export",
            isError: false,
          },
        },
        {
          eventId: "cmp-1",
          sessionId: "run:run-1:runtime",
          sequence: 4,
          eventType: RuntimeSessionEventType.COMPACTION,
          timestamp: "2026-05-10T10:00:05.000Z",
          parentSessionId: "",
          taskId: "",
          workerId: "",
          payload: {
            runId: "run-1",
            entryId: "entry-redacted",
            entryIds: ["entry-redacted"],
            entryCount: 1,
            components: "session_reports",
            ledgerPath: "/runs/run-1/compactions.jsonl",
            latestEntryPath: "/runs/run-1/compactions.latest",
            generation: 2,
            summary: "do-not-export",
          },
        },
      ],
    });
    const childLog = RuntimeSessionEventLog.fromJSON({
      sessionId: "task:run:run-1:runtime:retry:w-1",
      parentSessionId: "run:run-1:runtime",
      taskId: "retry",
      workerId: "w-1",
      metadata: { role: "coach", secret: "do-not-export" },
      createdAt: "2026-05-10T10:00:02.500Z",
      updatedAt: "2026-05-10T10:00:03.000Z",
      events: [
        {
          eventId: "child-prompt",
          sessionId: "task:run:run-1:runtime:retry:w-1",
          sequence: 0,
          eventType: RuntimeSessionEventType.PROMPT_SUBMITTED,
          timestamp: "2026-05-10T10:00:02.500Z",
          parentSessionId: "run:run-1:runtime",
          taskId: "retry",
          workerId: "w-1",
          payload: {
            role: "coach",
            prompt: "child prompt text",
            cwd: "/workspace",
          },
        },
        {
          eventId: "child-answer",
          sessionId: "task:run:run-1:runtime:retry:w-1",
          sequence: 1,
          eventType: RuntimeSessionEventType.ASSISTANT_MESSAGE,
          timestamp: "2026-05-10T10:00:03.000Z",
          parentSessionId: "run:run-1:runtime",
          taskId: "retry",
          workerId: "w-1",
          payload: {
            role: "coach",
            text: "child answer text",
            metadata: { secret: "do-not-export" },
          },
        },
      ],
    });

    const trace = runtimeSessionLogToRunTrace(parentLog, { childLogs: [childLog] });

    expect(trace.runId).toBe("run-1");
    expect(trace.scenarioType).toBe("grid_ctf");
    expect(trace.createdAt).toBe("2026-05-10T10:00:00.000Z");
    expect(trace.events.map((event) => event.eventType)).toEqual([
      "runtime_prompt_submitted",
      "runtime_shell_command",
      "runtime_child_task_started",
      "runtime_prompt_submitted",
      "runtime_assistant_message",
      "runtime_child_task_completed",
      "runtime_compaction",
    ]);

    expect(trace.events[0].actor.toDict()).toEqual({
      actor_type: "role",
      actor_id: "analyst",
      actor_name: "analyst",
    });
    expect(trace.events[0].payload).toMatchObject({
      runtime_session_id: "run:run-1:runtime",
      runtime_event_id: "prompt-1",
      runtime_event_type: "prompt_submitted",
      sequence: 0,
      request_id: "req-1",
      role: "analyst",
      cwd: "/workspace",
    });
    expect(trace.events[0].payload).not.toHaveProperty("prompt");

    expect(trace.events[1].payload).toMatchObject({
      command_name: "verify",
      phase: "end",
      exit_code: 0,
      args_summary: "verify --quick",
    });
    expect(trace.events[1].payload).not.toHaveProperty("stdout");

    expect(trace.events[3].payload).toMatchObject({
      parent_session_id: "run:run-1:runtime",
      task_id: "retry",
      worker_id: "w-1",
    });
    expect(trace.events[6].payload).toMatchObject({
      entry_id: "entry-redacted",
      entry_ids: ["entry-redacted"],
      entry_count: 1,
      components: "session_reports",
      ledger_path: "/runs/run-1/compactions.jsonl",
      latest_entry_path: "/runs/run-1/compactions.latest",
      generation: 2,
    });

    expect(JSON.stringify(trace.toDict())).not.toContain("do-not-export");
    expect(JSON.stringify(trace.toDict())).not.toContain("secret prompt text");
    expect(JSON.stringify(trace.toDict())).not.toContain("child answer text");
  });
});
