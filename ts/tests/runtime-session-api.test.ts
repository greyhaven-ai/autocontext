import { describe, expect, it, vi } from "vitest";

import {
  buildRuntimeSessionApiRoutes,
  runtimeSessionDiscoveryForRun,
  runtimeSessionUrlForRun,
} from "../src/server/runtime-session-api.js";
import {
  RuntimeSessionEventLog,
  RuntimeSessionEventType,
} from "../src/session/runtime-events.js";

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

describe("runtime session HTTP API routes", () => {
  it("builds stable run discovery links and optional summaries", () => {
    const load = vi.fn(() => createLog());

    expect(runtimeSessionUrlForRun("abc/needs space")).toBe(
      "/api/cockpit/runs/abc%2Fneeds%20space/runtime-session",
    );
    expect(runtimeSessionDiscoveryForRun({ list: vi.fn(), load }, "abc")).toMatchObject({
      runtime_session: {
        session_id: "run:abc:runtime",
        event_count: 1,
      },
      runtime_session_url: "/api/cockpit/runs/abc/runtime-session",
    });
    expect(load).toHaveBeenCalledWith("run:abc:runtime");
    expect(runtimeSessionDiscoveryForRun(null, "abc")).toEqual({
      runtime_session: null,
      runtime_session_url: "/api/cockpit/runs/abc/runtime-session",
    });
  });

  it("lists runtime session summaries through the read store port", () => {
    const close = vi.fn();
    const list = vi.fn(() => [createLog()]);
    const load = vi.fn();
    const api = buildRuntimeSessionApiRoutes({
      openStore: () => ({ list, load, close }),
    });

    const response = api.list(new URLSearchParams("limit=5"));

    expect(response.status).toBe(200);
    expect(list).toHaveBeenCalledWith({ limit: 5 });
    expect(close).toHaveBeenCalled();
    expect(response.body).toEqual({
      sessions: [
        {
          session_id: "run:abc:runtime",
          parent_session_id: "",
          task_id: "",
          worker_id: "",
          goal: "autoctx run support_triage",
          event_count: 1,
          created_at: "2026-04-10T00:00:00.000Z",
          updated_at: "2026-04-10T00:00:02.000Z",
        },
      ],
    });
  });

  it("reads event logs by explicit session id and by run id", () => {
    const load = vi.fn((sessionId: string) => createLog(sessionId));
    const api = buildRuntimeSessionApiRoutes({
      openStore: () => ({ list: vi.fn(), load }),
    });

    const bySession = api.getBySessionId("custom-session");
    expect(load).toHaveBeenCalledWith("custom-session");
    expect((bySession.body as Record<string, unknown>).sessionId).toBe("custom-session");

    const byRun = api.getByRunId("abc");
    expect(load).toHaveBeenCalledWith("run:abc:runtime");
    expect((byRun.body as Record<string, unknown>).sessionId).toBe("run:abc:runtime");
  });

  it("reads timelines by explicit session id and by run id", () => {
    const load = vi.fn((sessionId: string) => createLog(sessionId));
    const api = buildRuntimeSessionApiRoutes({
      openStore: () => ({ list: vi.fn(), load }),
    });

    const bySession = api.getTimelineBySessionId("custom-session");
    expect(load).toHaveBeenCalledWith("custom-session");
    expect((bySession.body as Record<string, unknown>).summary).toMatchObject({
      session_id: "custom-session",
    });

    const byRun = api.getTimelineByRunId("abc");
    expect(load).toHaveBeenCalledWith("run:abc:runtime");
    expect((byRun.body as Record<string, unknown>).items).toEqual([
      expect.objectContaining({
        kind: "prompt",
        status: "in_flight",
        prompt_preview: "Improve support replies",
      }),
    ]);
  });

  it("returns stable validation and not-found responses", () => {
    const api = buildRuntimeSessionApiRoutes({
      openStore: () => ({ list: vi.fn(), load: vi.fn(() => null) }),
    });

    expect(api.list(new URLSearchParams("limit=0"))).toEqual({
      status: 422,
      body: { detail: "limit must be a positive integer" },
    });
    expect(api.getBySessionId("missing")).toEqual({
      status: 404,
      body: { detail: "Runtime session 'missing' not found", session_id: "missing" },
    });
    expect(api.getByRunId("missing")).toEqual({
      status: 404,
      body: {
        detail: "Runtime session for run 'missing' not found",
        session_id: "run:missing:runtime",
      },
    });
    expect(api.getTimelineByRunId("missing")).toEqual({
      status: 404,
      body: {
        detail: "Runtime session timeline for run 'missing' not found",
        session_id: "run:missing:runtime",
      },
    });
  });
});
