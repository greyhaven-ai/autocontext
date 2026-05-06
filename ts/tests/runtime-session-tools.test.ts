import { describe, expect, it, vi } from "vitest";

import {
  buildRuntimeSessionIdentifierConflictPayload,
  buildRuntimeSessionIdentifierRequiredPayload,
  buildRuntimeSessionNotFoundPayload,
  registerRuntimeSessionTools,
} from "../src/mcp/runtime-session-tools.js";
import {
  RuntimeSessionEventLog,
  RuntimeSessionEventType,
} from "../src/session/runtime-events.js";

function createFakeServer() {
  const registeredTools: Record<
    string,
    {
      description: string;
      schema: Record<string, unknown>;
      handler: (args: Record<string, unknown>) => Promise<{ content: Array<{ type: string; text: string }> }>;
    }
  > = {};

  return {
    registeredTools,
    tool(
      name: string,
      description: string,
      schema: Record<string, unknown>,
      handler: (args: Record<string, unknown>) => Promise<{ content: Array<{ type: string; text: string }> }>,
    ) {
      registeredTools[name] = { description, schema, handler };
    },
  };
}

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

describe("runtime session MCP tools", () => {
  it("lists runtime session summaries through an injected read store", async () => {
    const server = createFakeServer();
    const list = vi.fn(() => [createLog()]);
    const load = vi.fn();

    registerRuntimeSessionTools(server, {
      store: { list, load },
    });

    const result = await server.registeredTools.list_runtime_sessions.handler({
      limit: 5,
    });

    expect(list).toHaveBeenCalledWith({ limit: 5 });
    expect(JSON.parse(result.content[0].text)).toEqual({
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

  it("returns a runtime session by run id or session id", async () => {
    const server = createFakeServer();
    const list = vi.fn();
    const load = vi.fn((sessionId: string) => createLog(sessionId));

    registerRuntimeSessionTools(server, {
      store: { list, load },
    });

    const byRunId = await server.registeredTools.get_runtime_session.handler({
      runId: "abc",
    });
    expect(load).toHaveBeenCalledWith("run:abc:runtime");
    expect(JSON.parse(byRunId.content[0].text).sessionId).toBe("run:abc:runtime");

    const bySessionId = await server.registeredTools.get_runtime_session.handler({
      sessionId: "custom-session",
    });
    expect(load).toHaveBeenCalledWith("custom-session");
    expect(JSON.parse(bySessionId.content[0].text).sessionId).toBe("custom-session");
  });

  it("returns a runtime-session timeline by run id or session id", async () => {
    const server = createFakeServer();
    const list = vi.fn();
    const load = vi.fn((sessionId: string) => createLog(sessionId));

    registerRuntimeSessionTools(server, {
      store: { list, load },
    });

    const byRunId = await server.registeredTools.get_runtime_session_timeline.handler({
      runId: "abc",
    });
    expect(load).toHaveBeenCalledWith("run:abc:runtime");
    expect(JSON.parse(byRunId.content[0].text).items[0]).toMatchObject({
      kind: "prompt",
      status: "in_flight",
      prompt_preview: "Improve support replies",
    });

    const bySessionId = await server.registeredTools.get_runtime_session_timeline.handler({
      sessionId: "custom-session",
    });
    expect(load).toHaveBeenCalledWith("custom-session");
    expect(JSON.parse(bySessionId.content[0].text).summary.session_id).toBe("custom-session");
  });

  it("returns stable validation and not-found payloads", async () => {
    const server = createFakeServer();
    const list = vi.fn();
    const load = vi.fn(() => null);

    registerRuntimeSessionTools(server, {
      store: { list, load },
    });

    const missingIdentifier = await server.registeredTools.get_runtime_session.handler({});
    expect(JSON.parse(missingIdentifier.content[0].text)).toEqual(
      buildRuntimeSessionIdentifierRequiredPayload(),
    );

    const conflictingIdentifier = await server.registeredTools.get_runtime_session.handler({
      sessionId: "run:abc:runtime",
      runId: "abc",
    });
    expect(JSON.parse(conflictingIdentifier.content[0].text)).toEqual(
      buildRuntimeSessionIdentifierConflictPayload(),
    );

    const notFound = await server.registeredTools.get_runtime_session.handler({
      runId: "missing",
    });
    expect(JSON.parse(notFound.content[0].text)).toEqual(
      buildRuntimeSessionNotFoundPayload("run:missing:runtime"),
    );
  });
});
