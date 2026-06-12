import { describe, expect, it, vi } from "vitest";

import { buildBackgroundSessionApiRoutes } from "../src/server/background-session-api.ts";
import { RuntimeSessionEventLog, RuntimeSessionEventType } from "../src/session/runtime-events.js";
import type { RunRow, TaskQueueRow } from "../src/storage/index.js";

function createLog(
  sessionId = "run:abc:runtime",
  opts: { taskId?: string; metadata?: Record<string, unknown> } = {},
): RuntimeSessionEventLog {
  return RuntimeSessionEventLog.fromJSON({
    sessionId,
    parentSessionId: "",
    taskId: opts.taskId ?? "task-abc",
    workerId: "worker-1",
    metadata: opts.metadata ?? {
      goal: "autoctx run support_triage",
      runId: "abc",
      status: "running",
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
        payload: { role: "default", requestId: "req-1", prompt: "SECRET_VALUE" },
        parentSessionId: "",
        taskId: "task-abc",
        workerId: "worker-1",
      },
    ],
  });
}

function createTask(id: string, status: string, specName = "autoctx run support_triage"): TaskQueueRow {
  return {
    id,
    spec_name: specName,
    status,
    priority: 0,
    config_json: null,
    scheduled_at: null,
    started_at: null,
    completed_at: null,
    best_score: null,
    best_output: null,
    total_rounds: null,
    met_threshold: 0,
    result_json: null,
    error: null,
    created_at: `2026-04-10T00:00:${id === "queued-1" ? "03" : "01"}.000Z`,
    updated_at: `2026-04-10T00:00:${id === "queued-1" ? "03" : "04"}.000Z`,
  };
}

function createRun(runId: string, status: string): RunRow {
  return {
    run_id: runId,
    scenario: "support_triage",
    target_generations: 1,
    executor_mode: "local",
    status,
    agent_provider: "",
    created_at: "2026-04-10T00:00:00.000Z",
    updated_at: "2026-04-10T00:00:05.000Z",
  };
}

function createChildLog(): RuntimeSessionEventLog {
  return RuntimeSessionEventLog.fromJSON({
    sessionId: "task:run:abc:runtime:child-1",
    parentSessionId: "run:abc:runtime",
    taskId: "child-1",
    workerId: "worker-child",
    metadata: { goal: "Inspect failing test", runId: "abc", status: "completed" },
    createdAt: "2026-04-10T00:00:03.000Z",
    updatedAt: "2026-04-10T00:00:04.000Z",
    events: [],
  });
}

describe("background session HTTP API routes", () => {
  it("lists background session summaries from runtime-session stores", () => {
    const close = vi.fn();
    const list = vi.fn(() => [createLog()]);
    const load = vi.fn();
    const listChildren = vi.fn(() => [createChildLog()]);
    const api = buildBackgroundSessionApiRoutes({
      openStore: () => ({ list, load, listChildren, close }),
    });

    const response = api.list(new URLSearchParams("limit=5"));

    expect(response.status).toBe(200);
    expect(list).toHaveBeenCalledWith({ limit: 5 });
    expect(close).toHaveBeenCalled();
    expect(response.body).toEqual({
      sessions: [
        expect.objectContaining({
          session_id: "run:abc:runtime",
          runtime_session_id: "run:abc:runtime",
          run_id: "abc",
          task_id: "task-abc",
          status: "running",
          event_count: 1,
          child_session_count: 1,
        }),
      ],
    });
  });

  it("aggregates backing tasks and queued task-only sessions", () => {
    const runtimeSession = createLog("run:completed:runtime", {
      taskId: "task-completed",
      metadata: { goal: "autoctx run completed", runId: "completed" },
    });
    const queuedTask = createTask("queued-1", "pending", "autoctx run queued");
    const completedTask = createTask("task-completed", "completed", "autoctx run completed");
    const tasks = new Map([
      [queuedTask.id, queuedTask],
      [completedTask.id, completedTask],
    ]);
    const api = buildBackgroundSessionApiRoutes({
      openStore: () => ({ list: vi.fn(() => [runtimeSession]), load: vi.fn(() => null) }),
      openSourceStore: () => ({
        listTasks: vi.fn(() => [queuedTask, completedTask]),
        getTask: vi.fn((taskId: string) => tasks.get(taskId) ?? null),
        getRun: vi.fn((runId: string) => createRun(runId, "completed")),
      }),
    });

    const listResponse = api.list(new URLSearchParams("limit=5"));
    const sessions = (listResponse.body as { sessions: Array<Record<string, unknown>> }).sessions;
    const byId = new Map(sessions.map((session) => [session.session_id, session]));

    expect(listResponse.status).toBe(200);
    expect(byId.get("run:completed:runtime")).toMatchObject({
      session_id: "run:completed:runtime",
      task_id: "task-completed",
      status: "completed",
    });
    expect(byId.get("task:queued-1")).toMatchObject({
      session_id: "task:queued-1",
      runtime_session_id: "",
      task_id: "queued-1",
      status: "queued",
      goal: "autoctx run queued",
    });

    const taskDetailResponse = api.getBySessionId("task:queued-1");
    expect(taskDetailResponse.status).toBe(200);
    expect(taskDetailResponse.body).toMatchObject({
      summary: { session_id: "task:queued-1", status: "queued" },
      normalized_events: [],
    });
  });

  it("reads a detail view with child summaries and normalized events", () => {
    const load = vi.fn((sessionId: string) => createLog(sessionId));
    const listChildren = vi.fn(() => [createChildLog()]);
    const task = createTask("task-abc", "running", "autoctx run support_triage");
    task.config_json = JSON.stringify({
      trigger: {
        type: "github_webhook",
        actor: "octocat",
        token: "ghp_SECRET_VALUE",
        apiKey: "sk-SECRET_VALUE",
      },
    });
    const api = buildBackgroundSessionApiRoutes({
      openStore: () => ({ list: vi.fn(), load, listChildren }),
      openSourceStore: () => ({ getTask: vi.fn(() => task) }),
    });

    const response = api.getBySessionId("run:abc:runtime");
    const body = response.body as Record<string, unknown>;

    expect(response.status).toBe(200);
    expect(load).toHaveBeenCalledWith("run:abc:runtime");
    expect(body.summary).toMatchObject({ session_id: "run:abc:runtime" });
    expect(body.child_sessions).toEqual([
      expect.objectContaining({
        session_id: "task:run:abc:runtime:child-1",
        status: "completed",
      }),
    ]);
    expect(body.trigger).toEqual({
      type: "github_webhook",
      actor: "octocat",
      token: "[redacted]",
      apiKey: "[redacted]",
    });
    expect(body.normalized_events).toEqual([
      expect.objectContaining({ event: "prompt_started", source_event_type: "prompt_submitted" }),
    ]);
    expect(JSON.stringify(body)).not.toContain("SECRET_VALUE");
  });

  it("returns stable validation and not-found responses", () => {
    const api = buildBackgroundSessionApiRoutes({
      openStore: () => ({ list: vi.fn(), load: vi.fn(() => null) }),
    });

    expect(api.list(new URLSearchParams("limit=0"))).toEqual({
      status: 422,
      body: { detail: "limit must be a positive integer" },
    });
    expect(api.getBySessionId("   ")).toEqual({
      status: 422,
      body: { detail: "session_id is required" },
    });
    expect(api.getBySessionId("missing")).toEqual({
      status: 404,
      body: { detail: "Background session 'missing' not found", session_id: "missing" },
    });
  });
});
