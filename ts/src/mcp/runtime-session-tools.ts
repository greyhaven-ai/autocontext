import { z } from "zod";

import { RuntimeSessionEventStore } from "../session/runtime-events.js";
import { runtimeSessionIdForRun } from "../session/runtime-session-ids.js";
import {
  readRuntimeSessionById,
  readRuntimeSessionByRunId,
  readRuntimeSessionSummaries,
  type RuntimeSessionReadStore,
} from "../session/runtime-session-read-model.js";
import { buildRuntimeSessionTimeline } from "../session/runtime-session-timeline.js";

interface JsonToolResponse {
  content: Array<{
    type: "text";
    text: string;
  }>;
}

type McpToolRegistrar = {
  tool: (...args: any[]) => unknown;
};

type RuntimeSessionClosableReadStore = RuntimeSessionReadStore & {
  close?: () => void;
};

interface RuntimeSessionToolInternals {
  createEventStore(dbPath: string): RuntimeSessionClosableReadStore;
}

const defaultInternals: RuntimeSessionToolInternals = {
  createEventStore: (dbPath) => new RuntimeSessionEventStore(dbPath),
};

const ListRuntimeSessionsArgsSchema = z.object({
  limit: z.number().int().default(50).describe("Max runtime sessions to return"),
});
type ListRuntimeSessionsArgs = z.infer<typeof ListRuntimeSessionsArgsSchema>;

const GetRuntimeSessionArgsSchema = z.object({
  sessionId: z.string().optional().describe("Runtime session ID"),
  runId: z.string().optional().describe("Run ID for the run-scoped runtime session"),
});
type GetRuntimeSessionArgs = z.infer<typeof GetRuntimeSessionArgsSchema>;

export function buildRuntimeSessionIdentifierRequiredPayload(): { error: string } {
  return { error: "get_runtime_session requires sessionId or runId" };
}

export function buildRuntimeSessionIdentifierConflictPayload(): { error: string } {
  return { error: "get_runtime_session accepts only one of sessionId or runId" };
}

export function buildRuntimeSessionNotFoundPayload(sessionId: string): {
  error: string;
  session_id: string;
} {
  return { error: "Runtime session not found", session_id: sessionId };
}

export function registerRuntimeSessionTools(
  server: McpToolRegistrar,
  opts: {
    dbPath?: string;
    store?: RuntimeSessionReadStore;
    internals?: Partial<RuntimeSessionToolInternals>;
  },
): void {
  const internals: RuntimeSessionToolInternals = {
    ...defaultInternals,
    ...opts.internals,
  };

  server.tool(
    "list_runtime_sessions",
    "List recent runtime-session event logs",
    ListRuntimeSessionsArgsSchema.shape,
    async (args: ListRuntimeSessionsArgs) =>
      withRuntimeSessionStore(opts, internals, (store) =>
        jsonText(
          {
            sessions: readRuntimeSessionSummaries(store, {
              limit: args.limit ?? 50,
            }),
          },
          2,
        ),
      ),
  );

  server.tool(
    "get_runtime_session",
    "Read a runtime-session event log by session id or run id",
    GetRuntimeSessionArgsSchema.shape,
    async (args: GetRuntimeSessionArgs) =>
      withRuntimeSessionStore(opts, internals, (store) => {
        const sessionId = cleanIdentifier(args.sessionId);
        const runId = cleanIdentifier(args.runId);
        if (!sessionId && !runId) {
          return jsonText(buildRuntimeSessionIdentifierRequiredPayload());
        }
        if (sessionId && runId) {
          return jsonText(buildRuntimeSessionIdentifierConflictPayload());
        }

        const log = sessionId
          ? readRuntimeSessionById(store, sessionId)
          : readRuntimeSessionByRunId(store, runId);
        const resolvedSessionId = sessionId || runtimeSessionIdForRun(runId);
        if (!log) {
          return jsonText(buildRuntimeSessionNotFoundPayload(resolvedSessionId));
        }
        return jsonText(log.toJSON(), 2);
      }),
  );

  server.tool(
    "get_runtime_session_timeline",
    "Read an operator-facing runtime-session timeline by session id or run id",
    GetRuntimeSessionArgsSchema.shape,
    async (args: GetRuntimeSessionArgs) =>
      withRuntimeSessionStore(opts, internals, (store) => {
        const result = loadRuntimeSessionFromArgs(store, args);
        if ("error" in result) {
          return jsonText(result);
        }
        return jsonText(buildRuntimeSessionTimeline(result.log), 2);
      }),
  );
}

type RuntimeSessionLookupResult =
  | { log: NonNullable<ReturnType<typeof readRuntimeSessionById>> }
  | { error: string; session_id?: string };

function loadRuntimeSessionFromArgs(
  store: RuntimeSessionReadStore,
  args: GetRuntimeSessionArgs,
): RuntimeSessionLookupResult {
  const sessionId = cleanIdentifier(args.sessionId);
  const runId = cleanIdentifier(args.runId);
  if (!sessionId && !runId) {
    return buildRuntimeSessionIdentifierRequiredPayload();
  }
  if (sessionId && runId) {
    return buildRuntimeSessionIdentifierConflictPayload();
  }
  const log = sessionId
    ? readRuntimeSessionById(store, sessionId)
    : readRuntimeSessionByRunId(store, runId);
  const resolvedSessionId = sessionId || runtimeSessionIdForRun(runId);
  if (!log) {
    return buildRuntimeSessionNotFoundPayload(resolvedSessionId);
  }
  return { log };
}

function withRuntimeSessionStore(
  opts: {
    dbPath?: string;
    store?: RuntimeSessionReadStore;
  },
  internals: RuntimeSessionToolInternals,
  callback: (store: RuntimeSessionReadStore) => JsonToolResponse,
): JsonToolResponse {
  if (opts.store) {
    return callback(opts.store);
  }
  if (!opts.dbPath) {
    return jsonText({ error: "Runtime session store requires dbPath" });
  }

  const store = internals.createEventStore(opts.dbPath);
  try {
    return callback(store);
  } finally {
    store.close?.();
  }
}

function jsonText(payload: unknown, indent?: number): JsonToolResponse {
  return {
    content: [
      {
        type: "text",
        text: JSON.stringify(payload, null, indent),
      },
    ],
  };
}

function cleanIdentifier(value: string | undefined): string {
  return typeof value === "string" ? value.trim() : "";
}
