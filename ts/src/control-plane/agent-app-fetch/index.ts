import { agentOutputMetadata } from "../../runtimes/agent-output-metadata.js";
import type { AgentOutput, AgentRuntime } from "../../runtimes/base.js";
import type {
  AutoctxAgentContext,
  AutoctxAgentDescriptor,
  AutoctxAgentEnv,
  AutoctxAgentHandler,
  AutoctxAgentInitOptions,
  AutoctxAgentPromptOptions,
  AutoctxAgentRuntime,
  AutoctxAgentSession,
  AutoctxAgentSessionOptions,
  AutoctxLoadedAgent,
  MaybePromise,
} from "../../agent-runtime/index.js";
import type {
  RuntimeCommandGrant,
  RuntimeToolGrant,
  RuntimeWorkspaceEnv,
} from "../../runtimes/workspace-env.js";
import type { RuntimeSession, RuntimeSessionPromptResult } from "../../session/runtime-session.js";
import type { RuntimeSessionEventStore } from "../../session/runtime-events.js";
import type { RuntimeSessionEventSink } from "../../session/runtime-session-notifications.js";
import { createAgentAppFetchSessionEventStoreBridge } from "./session-event-store.js";
import type { AgentAppFetchSessionEventStore } from "./session-event-store.js";
import {
  createAgentAppFetchWorkspaceEnv,
  createInMemoryAgentAppFetchWorkspaceStore,
} from "./workspace-store.js";
import type { AgentAppFetchWorkspaceStore } from "./workspace-store.js";

export type AgentAppFetchTarget = "fetch";
export type AgentAppFetchAgentExtension = ".ts" | ".tsx" | ".mts" | ".js" | ".mjs";

export interface AgentAppFetchCatalogEntry<Payload = unknown, Result = unknown> {
  name: string;
  relativePath: string;
  extension: AgentAppFetchAgentExtension | string;
  triggers?: Record<string, unknown>;
  handler?: AutoctxAgentHandler<Payload, Result>;
  load?: () => MaybePromise<AutoctxLoadedAgent<Payload, Result>>;
}

export interface StaticAgentAppCatalogEntry<Payload = unknown, Result = unknown> {
  name: string;
  relativePath: string;
  extension: AgentAppFetchAgentExtension | string;
  triggers?: Record<string, unknown>;
  handler: AutoctxAgentHandler<Payload, Result>;
}

export interface AgentAppFetchHandlerOptions<Payload = unknown, Result = unknown> {
  catalog: readonly AgentAppFetchCatalogEntry<Payload, Result>[];
  env?: Record<string, string | undefined>;
  workspace?: RuntimeWorkspaceEnv;
  workspaceStore?: AgentAppFetchWorkspaceStore;
  runtime?: AgentRuntime;
  commands?: RuntimeCommandGrant[];
  tools?: RuntimeToolGrant[];
  eventStore?: RuntimeSessionEventStore;
  sessionEventStore?: AgentAppFetchSessionEventStore;
  eventSink?: RuntimeSessionEventSink;
  maxBodyBytes?: number;
}

export interface AgentAppFetchSuccessEnvelope {
  ok: true;
  agent: string;
  id: string;
  result: unknown;
}

export interface AgentAppFetchErrorEnvelope {
  ok: false;
  error: {
    code: string;
    message: string;
  };
}

export interface AgentAppFetchManifest {
  ok: true;
  target: AgentAppFetchTarget;
  agents: Array<{
    name: string;
    relativePath: string;
    extension: string;
    triggers?: Record<string, unknown>;
  }>;
}

export * from "./catalog-planner.js";
export * from "./session-event-store.js";
export * from "./workspace-store.js";

type FetchAgentContextOptions<Payload> = AgentAppFetchHandlerOptions<Payload> & {
  agent: AutoctxLoadedAgent<Payload>;
  id: string;
  payload: Payload;
  env: AutoctxAgentEnv;
  workspace: RuntimeWorkspaceEnv;
};

const DEFAULT_MAX_BODY_BYTES = 1_000_000;
const JSON_HEADERS = { "content-type": "application/json; charset=utf-8" };

export function createStaticAgentAppCatalog<Payload = unknown, Result = unknown>(
  entries: readonly StaticAgentAppCatalogEntry<Payload, Result>[],
): AgentAppFetchCatalogEntry<Payload, Result>[] {
  return entries.map((entry) => ({ ...entry }));
}

export function createAgentAppFetchHandler<Payload = unknown, Result = unknown>(
  options: AgentAppFetchHandlerOptions<Payload, Result>,
): (request: Request) => Promise<Response> {
  const catalog = [...options.catalog];
  const env = definedStringRecord(options.env ?? {});
  return async (request) =>
    handleAgentAppFetchRequest(request, {
      ...options,
      catalog,
      env,
      workspace:
        options.workspace ??
        createAgentAppFetchWorkspaceEnv({
          store: options.workspaceStore ?? createInMemoryAgentAppFetchWorkspaceStore(),
        }),
    });
}

export async function handleAgentAppFetchRequest<Payload = unknown, Result = unknown>(
  request: Request,
  options: AgentAppFetchHandlerOptions<Payload, Result> & {
    env: AutoctxAgentEnv;
    workspace: RuntimeWorkspaceEnv;
  },
): Promise<Response> {
  const sessionEventBridge = options.sessionEventStore
    ? createAgentAppFetchSessionEventStoreBridge(options.sessionEventStore)
    : undefined;
  const effectiveEventStore = (options.eventStore ?? sessionEventBridge?.eventStore) as
    | RuntimeSessionEventStore
    | undefined;
  const effectiveOptions = {
    ...options,
    eventStore: effectiveEventStore,
  };

  try {
    const url = new URL(request.url);
    if (request.method === "GET" && (url.pathname === "/manifest" || url.pathname === "/agents")) {
      return jsonResponse(200, buildManifest(effectiveOptions.catalog));
    }

    const match = /^\/agents\/([^/]+)\/invoke$/.exec(url.pathname);
    if (request.method === "POST" && match) {
      const agentName = decodeURIComponent(match[1]!);
      const entry = resolveCatalogEntry(effectiveOptions.catalog, agentName);
      if (!entry) {
        return jsonResponse(404, renderAgentNotFound(agentName, effectiveOptions.catalog));
      }
      const body = await readJsonRequestBody(
        request,
        effectiveOptions.maxBodyBytes ?? DEFAULT_MAX_BODY_BYTES,
      );
      const loaded = await loadCatalogEntry(entry);
      const id = readOptionalString(body.id) ?? "default";
      const payload = ("payload" in body ? body.payload : {}) as Payload;
      const result = await loaded.handler(
        createFetchAgentContext({
          ...effectiveOptions,
          agent: loaded,
          id,
          payload,
        }),
      );
      await sessionEventBridge?.flush();
      return jsonResponse(200, {
        ok: true,
        agent: loaded.name,
        id,
        result,
      } satisfies AgentAppFetchSuccessEnvelope);
    }

    return jsonResponse(404, {
      ok: false,
      error: {
        code: "AUTOCTX_AGENT_NOT_FOUND",
        message: `No Fetch agent app route for ${request.method} ${url.pathname}`,
      },
    } satisfies AgentAppFetchErrorEnvelope);
  } catch (error) {
    try {
      await sessionEventBridge?.flush();
    } catch (flushError) {
      return jsonResponse(500, {
        ok: false,
        error: {
          code: "AUTOCTX_AGENT_APP_SESSION_EVENT_STORE_ERROR",
          message: flushError instanceof Error ? flushError.message : String(flushError),
        },
      } satisfies AgentAppFetchErrorEnvelope);
    }
    if (error instanceof AgentAppFetchRequestError) {
      return jsonResponse(error.statusCode, {
        ok: false,
        error: {
          code: error.code,
          message: error.message,
        },
      } satisfies AgentAppFetchErrorEnvelope);
    }
    return jsonResponse(500, {
      ok: false,
      error: {
        code: "AUTOCTX_AGENT_APP_ERROR",
        message: error instanceof Error ? error.message : String(error),
      },
    } satisfies AgentAppFetchErrorEnvelope);
  }
}

function buildManifest<Payload, Result>(
  catalog: readonly AgentAppFetchCatalogEntry<Payload, Result>[],
): AgentAppFetchManifest {
  return {
    ok: true,
    target: "fetch",
    agents: catalog.map((entry) => ({
      name: entry.name,
      relativePath: entry.relativePath,
      extension: entry.extension,
      triggers: cloneRecord(entry.triggers),
    })),
  };
}

function resolveCatalogEntry<Payload, Result>(
  catalog: readonly AgentAppFetchCatalogEntry<Payload, Result>[],
  agentName: string,
): AgentAppFetchCatalogEntry<Payload, Result> | undefined {
  return catalog.find((entry) => entry.name === agentName);
}

function renderAgentNotFound<Payload, Result>(
  agentName: string,
  catalog: readonly AgentAppFetchCatalogEntry<Payload, Result>[],
): AgentAppFetchErrorEnvelope {
  const available = catalog.map((entry) => entry.name).join(", ");
  return {
    ok: false,
    error: {
      code: "AUTOCTX_AGENT_NOT_FOUND",
      message: available
        ? `AutoContext agent not found: ${agentName}. Available: ${available}`
        : `AutoContext agent not found: ${agentName}. No handlers registered in the static catalog`,
    },
  };
}

async function loadCatalogEntry<Payload, Result>(
  entry: AgentAppFetchCatalogEntry<Payload, Result>,
): Promise<AutoctxLoadedAgent<Payload, Result>> {
  if (entry.handler) {
    return {
      name: entry.name,
      relativePath: entry.relativePath,
      handler: entry.handler,
      triggers: entry.triggers,
    };
  }
  if (!entry.load) {
    throw new Error(`AutoContext agent '${entry.name}' must provide a handler or load function`);
  }
  const loaded = await entry.load();
  return {
    ...loaded,
    name: loaded.name || entry.name,
    relativePath: loaded.relativePath ?? entry.relativePath,
    triggers: loaded.triggers ?? entry.triggers,
  };
}

function createFetchAgentContext<Payload>(
  options: FetchAgentContextOptions<Payload>,
): AutoctxAgentContext<Payload> {
  const agent: AutoctxAgentDescriptor = {
    name: options.agent.name,
    path: options.agent.path,
    relativePath: options.agent.relativePath,
  };
  return {
    id: options.id,
    payload: options.payload,
    env: Object.freeze({ ...options.env }),
    workspace: options.workspace,
    agent,
    init: async (initOptions: AutoctxAgentInitOptions = {}) =>
      new FetchRuntimeBackedAutoctxAgent({
        agent,
        workspace: options.workspace,
        runtime: initOptions.runtime ?? options.runtime,
        cwd: initOptions.cwd,
        commands: [...(options.commands ?? []), ...(initOptions.commands ?? [])],
        tools: [...(options.tools ?? []), ...(initOptions.tools ?? [])],
        eventStore: initOptions.eventStore ?? options.eventStore,
        eventSink: initOptions.eventSink ?? options.eventSink,
        metadata: initOptions.metadata,
        goal: initOptions.goal,
      }),
  };
}

class FetchRuntimeBackedAutoctxAgent implements AutoctxAgentRuntime {
  readonly #agent: AutoctxAgentDescriptor;
  readonly #workspace: RuntimeWorkspaceEnv;
  readonly #runtime?: AgentRuntime;
  readonly #goal?: string;
  readonly #cwd?: string;
  readonly #commands: RuntimeCommandGrant[];
  readonly #tools: RuntimeToolGrant[];
  readonly #eventStore?: RuntimeSessionEventStore;
  readonly #eventSink?: RuntimeSessionEventSink;
  readonly #metadata?: Record<string, unknown>;
  readonly #sessions = new Map<string, AutoctxAgentSession>();

  constructor(options: {
    agent: AutoctxAgentDescriptor;
    workspace: RuntimeWorkspaceEnv;
    runtime?: AgentRuntime;
    goal?: string;
    cwd?: string;
    commands?: RuntimeCommandGrant[];
    tools?: RuntimeToolGrant[];
    eventStore?: RuntimeSessionEventStore;
    eventSink?: RuntimeSessionEventSink;
    metadata?: Record<string, unknown>;
  }) {
    this.#agent = options.agent;
    this.#workspace = options.workspace;
    this.#runtime = options.runtime;
    this.#goal = options.goal;
    this.#cwd = options.cwd;
    this.#commands = options.commands ?? [];
    this.#tools = options.tools ?? [];
    this.#eventStore = options.eventStore;
    this.#eventSink = options.eventSink;
    this.#metadata = options.metadata;
  }

  async session(
    sessionKey = "default",
    options: AutoctxAgentSessionOptions = {},
  ): Promise<AutoctxAgentSession> {
    const cacheKey = options.sessionId ?? sessionKey;
    const existing = this.#sessions.get(cacheKey);
    if (existing) return existing;
    const { RuntimeSession } = await import("../../session/runtime-session.js");
    const session = RuntimeSession.create({
      sessionId: options.sessionId ?? autoctxAgentSessionId(this.#agent.name, sessionKey),
      goal: options.goal ?? this.#goal ?? `AutoContext agent ${this.#agent.name}`,
      workspace: this.#workspace,
      eventStore: options.eventStore ?? this.#eventStore,
      eventSink: options.eventSink ?? this.#eventSink,
      metadata: {
        ...(this.#metadata ?? {}),
        ...(options.metadata ?? {}),
        agentName: this.#agent.name,
        agentPath: this.#agent.path,
        agentSessionKey: sessionKey,
        experimentalAgentRuntime: true,
      },
    });
    const handle = new FetchRuntimeBackedAutoctxAgentSession({
      session,
      runtime: this.#runtime,
      cwd: options.cwd ?? this.#cwd,
      commands: [...this.#commands, ...(options.commands ?? [])],
      tools: [...this.#tools, ...(options.tools ?? [])],
    });
    this.#sessions.set(cacheKey, handle);
    return handle;
  }

  close(): void {
    this.#runtime?.close?.();
  }
}

class FetchRuntimeBackedAutoctxAgentSession implements AutoctxAgentSession {
  readonly session: RuntimeSession;
  readonly #runtime?: AgentRuntime;
  readonly #cwd?: string;
  readonly #commands: RuntimeCommandGrant[];
  readonly #tools: RuntimeToolGrant[];

  constructor(options: {
    session: RuntimeSession;
    runtime?: AgentRuntime;
    cwd?: string;
    commands?: RuntimeCommandGrant[];
    tools?: RuntimeToolGrant[];
  }) {
    this.session = options.session;
    this.#runtime = options.runtime;
    this.#cwd = options.cwd;
    this.#commands = options.commands ?? [];
    this.#tools = options.tools ?? [];
  }

  async prompt(
    prompt: string,
    options: AutoctxAgentPromptOptions = {},
  ): Promise<RuntimeSessionPromptResult> {
    const runtime = options.runtime ?? this.#runtime;
    if (!runtime) {
      throw new Error("AutoContext agent session prompt requires an AgentRuntime");
    }
    return this.session.submitPrompt({
      prompt,
      role: options.role,
      cwd: options.cwd ?? this.#cwd,
      commands: [...this.#commands, ...(options.commands ?? [])],
      tools: [...this.#tools, ...(options.tools ?? [])],
      handler: async () => {
        const output = await runtime.generate({
          prompt,
          system: options.system,
          schema: options.schema,
        });
        return {
          text: output.text,
          metadata: agentPromptMetadata(runtime, output, this.session.sessionId),
        };
      },
    });
  }
}

class AgentAppFetchRequestError extends Error {
  readonly statusCode: number;
  readonly code: string;

  constructor(statusCode: number, code: string, message: string) {
    super(message);
    this.statusCode = statusCode;
    this.code = code;
  }
}

async function readJsonRequestBody(
  request: Request,
  maxBodyBytes: number,
): Promise<Record<string, unknown>> {
  const text = await readLimitedRequestText(request, maxBodyBytes);
  if (!text.trim()) return {};
  try {
    const parsed: unknown = JSON.parse(text);
    if (!isRecord(parsed)) {
      throw new Error("body must be a JSON object");
    }
    return parsed;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new AgentAppFetchRequestError(
      400,
      "AUTOCTX_AGENT_APP_BAD_REQUEST",
      `Request body must be valid JSON: ${message}`,
    );
  }
}

async function readLimitedRequestText(request: Request, maxBodyBytes: number): Promise<string> {
  const advertisedLength = readContentLength(request.headers.get("content-length"));
  if (advertisedLength !== undefined && advertisedLength > maxBodyBytes) {
    throw requestTooLargeError();
  }
  if (!request.body) return "";

  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let totalBytes = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      totalBytes += value.byteLength;
      if (totalBytes > maxBodyBytes) {
        await cancelRequestReader(reader);
        throw requestTooLargeError();
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  return decodeRequestChunks(chunks, totalBytes);
}

async function cancelRequestReader(reader: ReadableStreamDefaultReader<Uint8Array>): Promise<void> {
  try {
    await reader.cancel("Request body is too large");
  } catch {
    // The adapter already knows the request is too large; ignore cancellation races.
  }
}

function decodeRequestChunks(chunks: Uint8Array[], totalBytes: number): string {
  if (chunks.length === 0) return "";
  if (chunks.length === 1) return new TextDecoder().decode(chunks[0]);
  const bytes = new Uint8Array(totalBytes);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return new TextDecoder().decode(bytes);
}

function readContentLength(value: string | null): number | undefined {
  const trimmed = value?.trim();
  if (!trimmed || !/^\d+$/.test(trimmed)) return undefined;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : Number.POSITIVE_INFINITY;
}

function requestTooLargeError(): AgentAppFetchRequestError {
  return new AgentAppFetchRequestError(
    413,
    "AUTOCTX_AGENT_APP_REQUEST_TOO_LARGE",
    "Request body is too large",
  );
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(`${JSON.stringify(body, null, 2)}\n`, {
    status,
    headers: JSON_HEADERS,
  });
}

function definedStringRecord(values: Record<string, string | undefined>): Record<string, string> {
  const result: Record<string, string> = {};
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined) result[key] = value;
  }
  return result;
}

function cloneRecord(
  value: Record<string, unknown> | undefined,
): Record<string, unknown> | undefined {
  return value ? { ...value } : undefined;
}

function readOptionalString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function autoctxAgentSessionId(agentName: string, sessionKey: string): string {
  return `agent:${safeSessionSegment(agentName)}:${safeSessionSegment(sessionKey)}`;
}

function safeSessionSegment(value: string): string {
  const normalized = value
    .trim()
    .replace(/[^A-Za-z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return normalized || "default";
}

function agentPromptMetadata(
  runtime: AgentRuntime,
  output: AgentOutput,
  runtimeSessionId: string,
): Record<string, unknown> {
  return {
    ...agentOutputMetadata(runtime.name, output, { runtimeSessionId }),
    experimentalAgentRuntime: true,
  };
}
