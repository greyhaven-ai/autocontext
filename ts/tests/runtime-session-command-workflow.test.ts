import { describe, expect, it, vi } from "vitest";

import {
  executeRuntimeSessionsCommandWorkflow,
  planRuntimeSessionsCommand,
  renderRuntimeSessionList,
  renderRuntimeSessionShow,
  renderRuntimeSessionTimeline,
  RUNTIME_SESSIONS_HELP_TEXT,
  summarizeRuntimeSession,
} from "../src/cli/runtime-session-command-workflow.js";
import { buildRuntimeSessionTimeline } from "../src/session/runtime-session-timeline.js";
import {
  RuntimeSessionEventLog,
  RuntimeSessionEventType,
} from "../src/session/runtime-events.js";

function createLog(): RuntimeSessionEventLog {
  return RuntimeSessionEventLog.fromJSON({
    sessionId: "run:abc:runtime",
    parentSessionId: "",
    taskId: "",
    workerId: "",
    metadata: {
      goal: "autoctx run support_triage",
      command: "run",
      runId: "abc",
      scenarioName: "support_triage",
    },
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
          role: "default",
          prompt: "Improve support replies",
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
          role: "default",
          text: "Candidate prompt",
        },
        parentSessionId: "",
        taskId: "",
        workerId: "",
      },
    ],
  });
}

describe("runtime-sessions command workflow", () => {
  it("exposes stable help text", () => {
    expect(RUNTIME_SESSIONS_HELP_TEXT).toContain("autoctx runtime-sessions");
    expect(RUNTIME_SESSIONS_HELP_TEXT).toContain("list");
    expect(RUNTIME_SESSIONS_HELP_TEXT).toContain("show");
    expect(RUNTIME_SESSIONS_HELP_TEXT).toContain("timeline");
    expect(RUNTIME_SESSIONS_HELP_TEXT).toContain("--json");
  });

  it("plans list and show subcommands", () => {
    expect(
      planRuntimeSessionsCommand({ limit: "25", json: true }, ["list"]),
    ).toEqual({ action: "list", limit: 25, json: true });
    expect(
      planRuntimeSessionsCommand({ id: "run:abc:runtime", json: false }, ["show"]),
    ).toEqual({ action: "show", sessionId: "run:abc:runtime", json: false });
    expect(
      planRuntimeSessionsCommand({ json: false }, ["show", "run:def:runtime"]),
    ).toEqual({ action: "show", sessionId: "run:def:runtime", json: false });
    expect(
      planRuntimeSessionsCommand({ "run-id": "abc", json: true }, ["show"]),
    ).toEqual({ action: "show", sessionId: "run:abc:runtime", json: true });
    expect(
      planRuntimeSessionsCommand({ "run-id": "abc", json: true }, ["timeline"]),
    ).toEqual({ action: "timeline", sessionId: "run:abc:runtime", json: true });
  });

  it("requires a session id for show", () => {
    expect(() => planRuntimeSessionsCommand({}, ["show"])).toThrow(
      "runtime-sessions show requires a session id",
    );
  });

  it("does not allow conflicting show identifiers", () => {
    expect(() =>
      planRuntimeSessionsCommand(
        { id: "run:abc:runtime", "run-id": "abc" },
        ["show"],
      ),
    ).toThrow("runtime-sessions show accepts only one of");
    expect(() =>
      planRuntimeSessionsCommand(
        { "run-id": "abc" },
        ["show", "run:abc:runtime"],
      ),
    ).toThrow("runtime-sessions show accepts only one of");
  });

  it("summarizes persisted runtime session logs", () => {
    expect(summarizeRuntimeSession(createLog())).toEqual({
      session_id: "run:abc:runtime",
      parent_session_id: "",
      task_id: "",
      worker_id: "",
      goal: "autoctx run support_triage",
      event_count: 2,
      created_at: "2026-04-10T00:00:00.000Z",
      updated_at: "2026-04-10T00:00:02.000Z",
    });
  });

  it("renders an empty list clearly", () => {
    expect(renderRuntimeSessionList([], false)).toBe("No runtime sessions found.");
  });

  it("renders session summaries as JSON", () => {
    const summary = summarizeRuntimeSession(createLog());

    expect(renderRuntimeSessionList([summary], true)).toBe(
      JSON.stringify({ sessions: [summary] }, null, 2),
    );
  });

  it("renders session summaries as human-readable rows", () => {
    expect(
      renderRuntimeSessionList([summarizeRuntimeSession(createLog())], false),
    ).toBe(
      "run:abc:runtime  events=2  goal=autoctx run support_triage  updated=2026-04-10T00:00:02.000Z",
    );
  });

  it("renders a session event log for inspection", () => {
    expect(renderRuntimeSessionShow(createLog(), false)).toContain(
      "Runtime session run:abc:runtime",
    );
    expect(renderRuntimeSessionShow(createLog(), false)).toContain(
      "1  assistant_message  role=default  text=Candidate prompt",
    );
  });

  it("renders a runtime-session timeline for operators", () => {
    const timeline = buildRuntimeSessionTimeline(createLog());

    expect(renderRuntimeSessionTimeline(timeline, false)).toContain(
      "Runtime session timeline run:abc:runtime",
    );
    expect(renderRuntimeSessionTimeline(timeline, false)).toContain(
      "0-1  prompt  completed  role=default  prompt=Improve support replies  response=Candidate prompt",
    );
    expect(renderRuntimeSessionTimeline(timeline, true)).toBe(
      JSON.stringify(timeline, null, 2),
    );
  });

  it("executes list workflow against the read store", () => {
    const list = vi.fn(() => [createLog()]);
    const load = vi.fn();

    const output = executeRuntimeSessionsCommandWorkflow({
      plan: { action: "list", limit: 5, json: false },
      store: { list, load },
    });

    expect(list).toHaveBeenCalledWith({ limit: 5 });
    expect(load).not.toHaveBeenCalled();
    expect(output).toContain("run:abc:runtime");
  });

  it("executes show workflow against the read store", () => {
    const list = vi.fn();
    const load = vi.fn(() => createLog());

    const output = executeRuntimeSessionsCommandWorkflow({
      plan: { action: "show", sessionId: "run:abc:runtime", json: true },
      store: { list, load },
    });

    expect(load).toHaveBeenCalledWith("run:abc:runtime");
    expect(list).not.toHaveBeenCalled();
    expect(JSON.parse(output).sessionId).toBe("run:abc:runtime");
  });

  it("executes timeline workflow against the read store", () => {
    const list = vi.fn();
    const load = vi.fn(() => createLog());

    const output = executeRuntimeSessionsCommandWorkflow({
      plan: { action: "timeline", sessionId: "run:abc:runtime", json: true },
      store: { list, load },
    });

    expect(load).toHaveBeenCalledWith("run:abc:runtime");
    expect(JSON.parse(output).items[0]).toMatchObject({
      kind: "prompt",
      status: "completed",
      prompt_preview: "Improve support replies",
      response_preview: "Candidate prompt",
    });
  });
});
