import { describe, expect, it } from "vitest";

import {
  buildBackgroundSessionDetail,
  buildBackgroundSessionSummary,
  backgroundSessionUrl,
  runtimeSessionUrl,
} from "../src/session/background-session-read-model.js";
import { RuntimeSessionEventLog, RuntimeSessionEventType } from "../src/session/runtime-events.js";
import type { TaskQueueRow } from "../src/storage/index.js";

function createRuntimeLog(): RuntimeSessionEventLog {
  return RuntimeSessionEventLog.fromJSON({
    sessionId: "run:run-123:runtime",
    parentSessionId: "",
    taskId: "task-123",
    workerId: "worker-1",
    metadata: {
      goal: "autoctx solve billing dispute replies",
      runId: "run-123",
      status: "running",
    },
    createdAt: "2026-06-01T00:00:00.000Z",
    updatedAt: "2026-06-01T00:01:00.000Z",
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
        payload: { command: "npm test", exitCode: 0 },
        parentSessionId: "",
        taskId: "task-123",
        workerId: "worker-1",
      },
    ],
  });
}

function createChildLog(): RuntimeSessionEventLog {
  return RuntimeSessionEventLog.fromJSON({
    sessionId: "task:run:run-123:runtime:child-1",
    parentSessionId: "run:run-123:runtime",
    taskId: "child-1",
    workerId: "worker-child",
    metadata: { goal: "Inspect failing test", runId: "run-123", status: "completed" },
    createdAt: "2026-06-01T00:00:30.000Z",
    updatedAt: "2026-06-01T00:00:45.000Z",
    events: [],
  });
}

function createTask(overrides: Partial<TaskQueueRow> = {}): TaskQueueRow {
  return {
    id: "task-123",
    spec_name: "billing_dispute_reply_task",
    status: "running",
    priority: 5,
    config_json: JSON.stringify({ trigger: { type: "manual", actor: "operator" } }),
    scheduled_at: null,
    started_at: "2026-06-01T00:00:05.000Z",
    completed_at: null,
    best_score: null,
    best_output: null,
    total_rounds: null,
    met_threshold: 0,
    result_json: null,
    error: null,
    created_at: "2026-06-01T00:00:00.000Z",
    updated_at: "2026-06-01T00:00:50.000Z",
    ...overrides,
  };
}

describe("background session read model", () => {
  it("summarizes runtime, task, artifact, and child-session sources without raw payloads", () => {
    const summary = buildBackgroundSessionSummary({
      runtimeSession: createRuntimeLog(),
      task: createTask(),
      artifacts: [
        {
          artifact_id: "trace-1",
          kind: "trace",
          label: "Runtime trace",
          path: "runs/run-123/trace.jsonl",
        },
        {
          artifact_id: "report-1",
          kind: "report",
          label: "Run report",
          path: "runs/run-123/report.md",
        },
      ],
      childSessions: [createChildLog()],
    });

    expect(summary).toEqual({
      session_id: "run:run-123:runtime",
      runtime_session_id: "run:run-123:runtime",
      run_id: "run-123",
      task_id: "task-123",
      parent_session_id: "",
      status: "running",
      goal: "autoctx solve billing dispute replies",
      event_count: 2,
      artifact_count: 2,
      child_session_count: 1,
      created_at: "2026-06-01T00:00:00.000Z",
      updated_at: "2026-06-01T00:01:00.000Z",
      result_url: "/api/cockpit/background-sessions/run%3Arun-123%3Aruntime",
      runtime_session_url: "/api/cockpit/runtime-sessions/run%3Arun-123%3Aruntime",
    });
    expect(JSON.stringify(summary)).not.toContain("SECRET_VALUE");
  });

  it("represents queued work before a runtime session exists", () => {
    const summary = buildBackgroundSessionSummary({
      task: createTask({
        id: "queued-1",
        status: "pending",
        started_at: null,
        updated_at: "2026-06-01T00:00:10.000Z",
      }),
    });

    expect(summary).toEqual({
      session_id: "task:queued-1",
      runtime_session_id: "",
      run_id: "",
      task_id: "queued-1",
      parent_session_id: "",
      status: "queued",
      goal: "billing_dispute_reply_task",
      event_count: 0,
      artifact_count: 0,
      child_session_count: 0,
      created_at: "2026-06-01T00:00:00.000Z",
      updated_at: "2026-06-01T00:00:10.000Z",
      result_url: "/api/cockpit/background-sessions/task%3Aqueued-1",
      runtime_session_url: "",
    });
  });

  it("builds an inspectable detail view with sanitized artifacts and child summaries", () => {
    const detail = buildBackgroundSessionDetail({
      runtimeSession: createRuntimeLog(),
      task: createTask(),
      artifacts: [
        {
          artifact_id: "pr-1",
          kind: "pull_request",
          label: "Review changes",
          url: "https://github.example/pr/1",
          metadata: { secret: "SECRET_VALUE", branch: "autoctx/run-123" },
        },
      ],
      childSessions: [createChildLog()],
    });

    expect(detail.summary.session_id).toBe("run:run-123:runtime");
    expect(detail.artifacts).toEqual([
      {
        artifact_id: "pr-1",
        kind: "pull_request",
        label: "Review changes",
        path: "",
        url: "https://github.example/pr/1",
      },
    ]);
    expect(detail.child_sessions).toEqual([
      expect.objectContaining({
        session_id: "task:run:run-123:runtime:child-1",
        parent_session_id: "run:run-123:runtime",
        status: "completed",
      }),
    ]);
    expect(detail.trigger).toEqual({ type: "manual", actor: "operator" });
    expect(JSON.stringify(detail)).not.toContain("SECRET_VALUE");
  });

  it("redacts sensitive trigger metadata from detail views", () => {
    const detail = buildBackgroundSessionDetail({
      task: createTask({
        config_json: JSON.stringify({
          trigger: {
            type: "github_webhook",
            actor: "octocat",
            retry: 2,
            dry_run: false,
            token: "ghp_SECRET_VALUE",
            github_token: "ghp_SECRET_VALUE",
            apiKey: "sk-SECRET_VALUE",
            clientSecret: "SECRET_VALUE",
            Authorization: "Bearer SECRET_VALUE",
            password: "SECRET_VALUE",
            private_key: "-----BEGIN PRIVATE KEY-----SECRET_VALUE",
            headers: { authorization: "Bearer SECRET_VALUE" },
          },
        }),
      }),
    });

    expect(detail.trigger).toEqual({
      type: "github_webhook",
      actor: "octocat",
      retry: 2,
      dry_run: false,
      token: "[redacted]",
      github_token: "[redacted]",
      apiKey: "[redacted]",
      clientSecret: "[redacted]",
      Authorization: "[redacted]",
      password: "[redacted]",
      private_key: "[redacted]",
    });
    expect(JSON.stringify(detail)).not.toContain("SECRET_VALUE");
  });

  it("uses shared URL helpers for encoded background and runtime session links", () => {
    expect(backgroundSessionUrl("run:run-123:runtime")).toBe(
      "/api/cockpit/background-sessions/run%3Arun-123%3Aruntime",
    );
    expect(runtimeSessionUrl("run:run-123:runtime")).toBe(
      "/api/cockpit/runtime-sessions/run%3Arun-123%3Aruntime",
    );
    expect(runtimeSessionUrl("")).toBe("");
  });
});
