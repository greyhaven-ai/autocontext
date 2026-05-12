import { promises as fs, type Dirent } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

import type { AgentOutput, AgentRuntime } from "../runtimes/base.js";
import {
  createInMemoryWorkspaceEnv,
  type RuntimeCommandGrant,
  type RuntimeToolGrant,
  type RuntimeWorkspaceEnv,
} from "../runtimes/workspace-env.js";
import {
  RuntimeSession,
  type RuntimeSessionPromptResult,
} from "../session/runtime-session.js";
import type { RuntimeSessionEventStore } from "../session/runtime-events.js";
import type { RuntimeSessionEventSink } from "../session/runtime-session-notifications.js";

export const AUTOCTX_AGENT_DIR = ".autoctx/agents";
export const AUTOCTX_AGENT_RUNTIME_EXPERIMENTAL = true;

type AutoctxAgentExtension = ".ts" | ".tsx" | ".mts" | ".js" | ".mjs";
const AUTOCTX_AGENT_EXTENSIONS: readonly AutoctxAgentExtension[] = [
  ".ts",
  ".tsx",
  ".mts",
  ".js",
  ".mjs",
];

export interface AutoctxAgentDescriptor {
  name: string;
  path?: string;
  relativePath?: string;
}

export interface AutoctxAgentEntry extends AutoctxAgentDescriptor {
  path: string;
  relativePath: string;
  extension: AutoctxAgentExtension;
}

export type AutoctxAgentTriggers = Record<string, unknown>;
export type AutoctxAgentEnv = Record<string, string | undefined>;
export type MaybePromise<T> = T | Promise<T>;

export interface AutoctxAgentContext<
  Payload = Record<string, unknown>,
> {
  payload: Payload;
  env: Readonly<AutoctxAgentEnv>;
  workspace: RuntimeWorkspaceEnv;
  agent: AutoctxAgentDescriptor;
  init(options?: AutoctxAgentInitOptions): Promise<AutoctxAgentRuntime>;
}

export type AutoctxAgentHandler<
  Payload = Record<string, unknown>,
  Result = unknown,
> = (context: AutoctxAgentContext<Payload>) => MaybePromise<Result>;

export interface AutoctxLoadedAgent<
  Payload = Record<string, unknown>,
  Result = unknown,
> extends AutoctxAgentDescriptor {
  handler: AutoctxAgentHandler<Payload, Result>;
  triggers?: AutoctxAgentTriggers;
}

export interface AutoctxAgentInitOptions {
  runtime?: AgentRuntime;
  model?: string;
  goal?: string;
  cwd?: string;
  commands?: RuntimeCommandGrant[];
  tools?: RuntimeToolGrant[];
  eventStore?: RuntimeSessionEventStore;
  eventSink?: RuntimeSessionEventSink;
  metadata?: Record<string, unknown>;
}

export interface AutoctxAgentSessionOptions {
  sessionId?: string;
  goal?: string;
  cwd?: string;
  commands?: RuntimeCommandGrant[];
  tools?: RuntimeToolGrant[];
  eventStore?: RuntimeSessionEventStore;
  eventSink?: RuntimeSessionEventSink;
  metadata?: Record<string, unknown>;
}

export interface AutoctxAgentPromptOptions {
  role?: string;
  cwd?: string;
  system?: string;
  schema?: Record<string, unknown>;
  commands?: RuntimeCommandGrant[];
  tools?: RuntimeToolGrant[];
  runtime?: AgentRuntime;
}

export interface AutoctxAgentSession {
  readonly session: RuntimeSession;
  prompt(prompt: string, options?: AutoctxAgentPromptOptions): Promise<RuntimeSessionPromptResult>;
}

export interface AutoctxAgentRuntime {
  session(sessionKey?: string, options?: AutoctxAgentSessionOptions): Promise<AutoctxAgentSession>;
  close(): void;
}

export interface AutoctxAgentDiscoveryOptions {
  cwd: string;
}

export interface AutoctxAgentInvocationOptions<
  Payload,
> {
  payload: Payload;
  env?: AutoctxAgentEnv;
  workspace?: RuntimeWorkspaceEnv;
  runtime?: AgentRuntime;
  agentName?: string;
  agentPath?: string;
  commands?: RuntimeCommandGrant[];
  tools?: RuntimeToolGrant[];
  eventStore?: RuntimeSessionEventStore;
  eventSink?: RuntimeSessionEventSink;
}

export async function discoverAutoctxAgents(
  options: AutoctxAgentDiscoveryOptions,
): Promise<AutoctxAgentEntry[]> {
  const cwd = path.resolve(options.cwd);
  const agentDir = path.join(cwd, AUTOCTX_AGENT_DIR);
  let entries: Dirent[];
  try {
    entries = await fs.readdir(agentDir, { withFileTypes: true });
  } catch (error) {
    if (isMissingPathError(error)) return [];
    throw error;
  }
  const agents: AutoctxAgentEntry[] = [];
  for (const entry of entries) {
    if (!entry.isFile()) continue;
    if (entry.name.startsWith(".")) continue;
    if (entry.name.endsWith(".d.ts")) continue;
    const extension = autoctxAgentExtension(entry.name);
    if (!extension) continue;
    const absolutePath = path.join(agentDir, entry.name);
    agents.push({
      name: path.basename(entry.name, extension),
      path: absolutePath,
      relativePath: toPosixPath(path.relative(cwd, absolutePath)),
      extension,
    });
  }
  return agents.sort((left, right) => left.name.localeCompare(right.name));
}

export async function loadAutoctxAgent<
  Payload = Record<string, unknown>,
  Result = unknown,
>(
  entry: AutoctxAgentEntry | string,
): Promise<AutoctxLoadedAgent<Payload, Result>> {
  const agentPath = typeof entry === "string" ? path.resolve(entry) : entry.path;
  const imported = await import(pathToFileURL(agentPath).href);
  const handler = imported.default;
  if (!isAutoctxAgentHandler<Payload, Result>(handler)) {
    throw new Error(`AutoContext agent '${agentPath}' must export a default handler function`);
  }
  const extension = autoctxAgentExtension(agentPath);
  const name = typeof entry === "string"
    ? path.basename(agentPath, extension ?? path.extname(agentPath))
    : entry.name;
  return {
    name,
    path: agentPath,
    relativePath: typeof entry === "string" ? undefined : entry.relativePath,
    handler,
    triggers: readRecord(imported.triggers),
  };
}

export async function invokeAutoctxAgent<
  Payload,
  Result = unknown,
>(
  agent: AutoctxLoadedAgent<Payload, Result> | AutoctxAgentHandler<Payload, Result>,
  options: AutoctxAgentInvocationOptions<Payload>,
): Promise<Awaited<Result>> {
  const loaded = normalizeLoadedAgent(agent, options);
  const context = createAutoctxAgentContext<Payload>({
    ...options,
    agentName: loaded.name,
    agentPath: loaded.path,
  });
  return await loaded.handler(context);
}

export function createAutoctxAgentContext<
  Payload,
>(
  options: AutoctxAgentInvocationOptions<Payload>,
): AutoctxAgentContext<Payload> {
  const workspace = options.workspace ?? createInMemoryWorkspaceEnv();
  const agent: AutoctxAgentDescriptor = {
    name: options.agentName ?? "agent",
    path: options.agentPath,
  };
  return {
    payload: options.payload,
    env: Object.freeze({ ...(options.env ?? {}) }),
    workspace,
    agent,
    init: async (initOptions = {}) =>
      new RuntimeBackedAutoctxAgent({
        agent,
        workspace,
        runtime: initOptions.runtime ?? options.runtime,
        model: initOptions.model,
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

interface RuntimeBackedAutoctxAgentOptions {
  agent: AutoctxAgentDescriptor;
  workspace: RuntimeWorkspaceEnv;
  runtime?: AgentRuntime;
  model?: string;
  goal?: string;
  cwd?: string;
  commands?: RuntimeCommandGrant[];
  tools?: RuntimeToolGrant[];
  eventStore?: RuntimeSessionEventStore;
  eventSink?: RuntimeSessionEventSink;
  metadata?: Record<string, unknown>;
}

class RuntimeBackedAutoctxAgent implements AutoctxAgentRuntime {
  readonly #agent: AutoctxAgentDescriptor;
  readonly #workspace: RuntimeWorkspaceEnv;
  readonly #runtime?: AgentRuntime;
  readonly #model?: string;
  readonly #goal?: string;
  readonly #cwd?: string;
  readonly #commands: RuntimeCommandGrant[];
  readonly #tools: RuntimeToolGrant[];
  readonly #eventStore?: RuntimeSessionEventStore;
  readonly #eventSink?: RuntimeSessionEventSink;
  readonly #metadata?: Record<string, unknown>;
  readonly #sessions = new Map<string, AutoctxAgentSession>();

  constructor(options: RuntimeBackedAutoctxAgentOptions) {
    this.#agent = options.agent;
    this.#workspace = options.workspace;
    this.#runtime = options.runtime;
    this.#model = options.model;
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
        runtimeModel: this.#model,
        experimentalAgentRuntime: true,
      },
    });
    const handle = new RuntimeBackedAutoctxAgentSession({
      session,
      runtime: this.#runtime,
      model: this.#model,
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

class RuntimeBackedAutoctxAgentSession implements AutoctxAgentSession {
  readonly session: RuntimeSession;
  readonly #runtime?: AgentRuntime;
  readonly #model?: string;
  readonly #cwd?: string;
  readonly #commands: RuntimeCommandGrant[];
  readonly #tools: RuntimeToolGrant[];

  constructor(options: {
    session: RuntimeSession;
    runtime?: AgentRuntime;
    model?: string;
    cwd?: string;
    commands?: RuntimeCommandGrant[];
    tools?: RuntimeToolGrant[];
  }) {
    this.session = options.session;
    this.#runtime = options.runtime;
    this.#model = options.model;
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
          metadata: agentPromptMetadata(runtime, output, this.#model, this.session.sessionId),
        };
      },
    });
  }
}

function normalizeLoadedAgent<
  Payload,
  Result,
>(
  agent: AutoctxLoadedAgent<Payload, Result> | AutoctxAgentHandler<Payload, Result>,
  options: AutoctxAgentInvocationOptions<Payload>,
): AutoctxLoadedAgent<Payload, Result> {
  if (typeof agent === "function") {
    return {
      name: options.agentName ?? "agent",
      path: options.agentPath,
      handler: agent,
    };
  }
  return agent;
}

function autoctxAgentSessionId(agentName: string, sessionKey: string): string {
  return `agent:${safeSessionSegment(agentName)}:${safeSessionSegment(sessionKey)}`;
}

function safeSessionSegment(value: string): string {
  const normalized = value.trim().replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "");
  return normalized || "default";
}

function autoctxAgentExtension(filePath: string): AutoctxAgentExtension | undefined {
  return AUTOCTX_AGENT_EXTENSIONS.find((extension) => filePath.endsWith(extension));
}

function readRecord(value: unknown): Record<string, unknown> | undefined {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return undefined;
  return Object.fromEntries(Object.entries(value));
}

function isMissingPathError(error: unknown): boolean {
  return hasErrorCode(error) && error.code === "ENOENT";
}

function hasErrorCode(error: unknown): error is { code: unknown } {
  return typeof error === "object" && error !== null && "code" in error;
}

function isAutoctxAgentHandler<Payload, Result>(
  value: unknown,
): value is AutoctxAgentHandler<Payload, Result> {
  return typeof value === "function";
}

function toPosixPath(value: string): string {
  return value.split(path.sep).join("/");
}

function agentPromptMetadata(
  runtime: AgentRuntime,
  output: AgentOutput,
  model: string | undefined,
  runtimeSessionId: string,
): Record<string, unknown> {
  return {
    ...(output.metadata ?? {}),
    runtime: runtime.name,
    model: output.model ?? model,
    costUsd: output.costUsd,
    structured: output.structured,
    runtimeSessionId,
    providerSessionId: output.sessionId,
    experimentalAgentRuntime: true,
  };
}
