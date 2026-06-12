import { existsSync, promises as fs, readFileSync } from "node:fs";
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import {
  discoverAutoctxAgents,
  invokeAutoctxAgent,
  loadAutoctxAgent,
} from "../../agent-runtime/index.js";
import type { AgentRuntime } from "../../runtimes/base.js";
import {
  createLocalWorkspaceEnv,
  type RuntimeWorkspaceEnv,
} from "../../runtimes/workspace-env.js";
import type { RuntimeSessionEventStore } from "../../session/runtime-events.js";
import type { RuntimeSessionEventSink } from "../../session/runtime-session-notifications.js";

export type NodeAgentAppBuildTarget = "node";

export interface NodeAgentAppBuildOptions {
  cwd: string;
  outDir?: string;
  packageName?: string;
  autoctxDependency?: string;
}

export interface NodeAgentAppBuildFile {
  relativePath: string;
  absolutePath: string;
  content: string;
}

export interface NodeAgentAppBuildPlan {
  target: NodeAgentAppBuildTarget;
  projectRoot: string;
  outputDir: string;
  handlerDir: ".autoctx/agents";
  routes: readonly ["GET /manifest", "POST /agents/:agent/invoke"];
  files: NodeAgentAppBuildFile[];
}

export interface NodeAgentAppBuildResult extends NodeAgentAppBuildPlan {
  writtenFiles: string[];
}

export interface NodeAgentRuntimeContracts {
  discoverAutoctxAgents: typeof discoverAutoctxAgents;
  loadAutoctxAgent: typeof loadAutoctxAgent;
  invokeAutoctxAgent: typeof invokeAutoctxAgent;
}

export type NodeAgentAppRuntimeHandle =
  | AgentRuntime
  | { runtime: AgentRuntime; close?: () => void | Promise<void> };

export interface NodeAgentAppRuntimeFactoryPlan {
  agentName: string;
  id: string;
  env: Readonly<Record<string, string>>;
}

export type NodeAgentAppRuntimeFactory = (
  plan: NodeAgentAppRuntimeFactoryPlan,
) => NodeAgentAppRuntimeHandle | undefined | Promise<NodeAgentAppRuntimeHandle | undefined>;

export interface NodeAgentAppServerOptions {
  projectRoot: string | URL;
  env?: Record<string, string | undefined>;
  envFile?: string;
  processEnv?: Record<string, string | undefined>;
  workspace?: RuntimeWorkspaceEnv;
  runtime?: AgentRuntime;
  createRuntime?: NodeAgentAppRuntimeFactory;
  agentRuntime?: NodeAgentRuntimeContracts;
  eventStore?: RuntimeSessionEventStore;
  eventSink?: RuntimeSessionEventSink;
  sessionDbPath?: string;
}

export interface StartNodeAgentAppServerOptions extends Partial<NodeAgentAppServerOptions> {
  projectRoot?: string | URL;
  host?: string;
  port?: number;
  runtimeModule?: string;
  stdout?: Pick<NodeJS.WriteStream, "write">;
}

interface NodeAgentAppSuccessEnvelope {
  ok: true;
  agent: string;
  id: string;
  result: unknown;
}

interface NodeAgentAppErrorEnvelope {
  ok: false;
  error: {
    code: string;
    message: string;
  };
}

const NODE_AGENT_ROUTES = ["GET /manifest", "POST /agents/:agent/invoke"] as const;
const DEFAULT_NODE_AGENT_OUTPUT_DIR = ".autoctx/build/node";
const AUTOCTX_PACKAGE_NAME = "autoctx";
const DEFAULT_AGENT_RUNTIME: NodeAgentRuntimeContracts = {
  discoverAutoctxAgents,
  loadAutoctxAgent,
  invokeAutoctxAgent,
};

export function planNodeAgentAppBuildTarget(
  options: NodeAgentAppBuildOptions,
): NodeAgentAppBuildPlan {
  const projectRoot = path.resolve(options.cwd);
  const outputDir = path.resolve(projectRoot, options.outDir ?? DEFAULT_NODE_AGENT_OUTPUT_DIR);
  const packageName = normalizePackageName(options.packageName, projectRoot);
  const files = [
    nodeAgentBuildFile(outputDir, "package.json", renderNodeAgentPackageJson({
      packageName,
      autoctxDependency: options.autoctxDependency ?? defaultAutoctxDependency(),
    })),
    nodeAgentBuildFile(
      outputDir,
      "server.mjs",
      renderNodeAgentServerEntrypoint(projectRootUrlSpecifier(outputDir, projectRoot)),
    ),
    nodeAgentBuildFile(outputDir, "README.md", renderNodeAgentReadme()),
    nodeAgentBuildFile(outputDir, ".gitignore", "node_modules\n.env\n*.sqlite\n*.sqlite-*\n"),
  ];
  return {
    target: "node",
    projectRoot,
    outputDir,
    handlerDir: ".autoctx/agents",
    routes: NODE_AGENT_ROUTES,
    files,
  };
}

export async function buildNodeAgentAppTarget(
  options: NodeAgentAppBuildOptions,
): Promise<NodeAgentAppBuildResult> {
  const plan = planNodeAgentAppBuildTarget(options);
  const writtenFiles: string[] = [];
  for (const file of plan.files) {
    await fs.mkdir(path.dirname(file.absolutePath), { recursive: true });
    await fs.writeFile(file.absolutePath, file.content, "utf-8");
    writtenFiles.push(file.absolutePath);
  }
  return { ...plan, writtenFiles };
}

export function renderNodeAgentServerEntrypoint(projectRootSpecifier = "../../.."): string {
  return `#!/usr/bin/env node
import * as agentRuntime from "autoctx/agent-runtime";
import { startNodeAgentAppServer } from "autoctx/control-plane/agent-app-node";

await startNodeAgentAppServer({
  projectRoot: new URL(${JSON.stringify(projectRootSpecifier)}, import.meta.url),
  agentRuntime,
});
`;
}

export async function createNodeAgentAppServer(
  options: NodeAgentAppServerOptions,
): Promise<Server> {
  const projectRoot = resolveProjectRoot(options.projectRoot);
  const workspace = options.workspace ?? createLocalWorkspaceEnv({ root: projectRoot });
  const eventStore = options.eventStore ?? await createEventStore(projectRoot, options.sessionDbPath);
  const agentRuntime = options.agentRuntime ?? DEFAULT_AGENT_RUNTIME;
  const env = await resolveExplicitEnv(projectRoot, options);
  const ownedWorkspace = options.workspace ? undefined : workspace;
  const ownedEventStore = options.eventStore ? undefined : eventStore;
  const server = createServer((request, response) => {
    void handleNodeAgentAppRequest(request, response, {
      ...options,
      projectRoot,
      workspace,
      eventStore,
      agentRuntime,
      env,
    });
  });
  server.once("close", () => {
    void ownedWorkspace?.cleanup();
    ownedEventStore?.close();
  });
  return server;
}

export async function startNodeAgentAppServer(
  options: StartNodeAgentAppServerOptions = {},
): Promise<Server> {
  const port = options.port ?? parsePort(process.env.AUTOCTX_AGENT_PORT ?? process.env.PORT ?? "3583");
  const host = options.host ?? process.env.AUTOCTX_AGENT_HOST ?? process.env.HOST ?? "127.0.0.1";
  const projectRoot = resolveProjectRoot(options.projectRoot ?? process.cwd());
  const runtimeModule = options.runtimeModule ?? process.env.AUTOCTX_RUNTIME_MODULE;
  const createRuntime = options.createRuntime ?? (runtimeModule
    ? await loadNodeAgentAppRuntimeFactory(runtimeModule, projectRoot)
    : undefined);
  const server = await createNodeAgentAppServer({
    projectRoot,
    env: options.env,
    envFile: options.envFile ?? process.env.AUTOCTX_ENV_FILE,
    processEnv: options.processEnv ?? process.env,
    workspace: options.workspace,
    runtime: options.runtime,
    createRuntime,
    eventStore: options.eventStore,
    eventSink: options.eventSink,
    sessionDbPath: options.sessionDbPath ?? process.env.AUTOCTX_SESSION_DB,
    agentRuntime: options.agentRuntime,
  });
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, host, () => resolve());
  });
  const address = server.address();
  const actualPort = typeof address === "object" && address ? address.port : port;
  const message = JSON.stringify({
    ok: true,
    target: "node",
    url: `http://${host}:${actualPort}`,
    manifest: `http://${host}:${actualPort}/manifest`,
  });
  (options.stdout ?? process.stdout).write(`${message}\n`);
  return server;
}

export async function loadNodeAgentAppEnvFile(
  envPath: string,
  processEnv: Record<string, string | undefined> = {},
): Promise<Record<string, string>> {
  const parsed = parseEnvFile(await fs.readFile(envPath, "utf-8"));
  const merged: Record<string, string> = {};
  for (const [key, value] of Object.entries(parsed)) {
    const shellValue = processEnv[key];
    merged[key] = shellValue === undefined ? value : shellValue;
  }
  return merged;
}

function defaultAutoctxDependency(): string {
  return `file:${toPosixPath(resolveAutoctxPackageRoot())}`;
}

function resolveAutoctxPackageRoot(): string {
  let current = path.dirname(fileURLToPath(import.meta.url));
  while (true) {
    const packageJsonPath = path.join(current, "package.json");
    if (existsSync(packageJsonPath)) {
      const packageJson = JSON.parse(readFileSync(packageJsonPath, "utf-8")) as { name?: unknown };
      if (packageJson.name === AUTOCTX_PACKAGE_NAME) return current;
    }
    const parent = path.dirname(current);
    if (parent === current) break;
    current = parent;
  }
  throw new Error("Unable to locate the installed autoctx package root for Node agent app generation");
}

function projectRootUrlSpecifier(outputDir: string, projectRoot: string): string {
  const relative = path.relative(outputDir, projectRoot).split(path.sep).join("/");
  return relative || ".";
}

function nodeAgentBuildFile(
  outputDir: string,
  relativePath: string,
  content: string,
): NodeAgentAppBuildFile {
  return {
    relativePath,
    absolutePath: path.join(outputDir, relativePath),
    content,
  };
}

function renderNodeAgentPackageJson(options: {
  packageName: string;
  autoctxDependency: string;
}): string {
  return `${JSON.stringify({
    name: options.packageName,
    private: true,
    type: "module",
    scripts: {
      start: "node server.mjs",
    },
    dependencies: {
      autoctx: options.autoctxDependency,
    },
  }, null, 2)}\n`;
}

function renderNodeAgentReadme(): string {
  return [
    "# AutoContext Node Agent App",
    "",
    "Generated by `autoctx agent build --target node`.",
    "",
    "This is a self-hosted Node wrapper around handlers in `.autoctx/agents`.",
    "It exposes the same HTTP shape as local `autoctx agent dev`:",
    "",
    "- `GET /manifest`",
    "- `POST /agents/<agent>/invoke`",
    "",
    "Runtime-backed handlers can receive host-created runtime capabilities via `AUTOCTX_RUNTIME_MODULE` (bare package specifier, relative/absolute file path, or URL).",
    "The generated package uses a local `file:` dependency on the currently installed `autoctx` so it does not reinstall a stale npm release before this target is published.",
    "Explicit handler env can be loaded with `AUTOCTX_ENV_FILE` (resolved from the source project root); the generated server does not capture the full host environment.",
    "Runtime-session events can be persisted with `AUTOCTX_SESSION_DB`.",
    "",
    "Boundary reference: docs/core-control-package-split.md#agent-app-build-targets",
    "",
  ].join("\n");
}

async function handleNodeAgentAppRequest(
  request: IncomingMessage,
  response: ServerResponse,
  options: NodeAgentAppServerOptions & {
    projectRoot: string;
    workspace: RuntimeWorkspaceEnv;
    agentRuntime: NodeAgentRuntimeContracts;
    env: Record<string, string>;
  },
): Promise<void> {
  try {
    const url = new URL(request.url ?? "/", "http://127.0.0.1");
    if (request.method === "GET" && (url.pathname === "/manifest" || url.pathname === "/agents")) {
      await writeJson(response, 200, await buildManifest(options.projectRoot, options.agentRuntime));
      return;
    }

    const match = /^\/agents\/([^/]+)\/invoke$/.exec(url.pathname);
    if (request.method === "POST" && match) {
      const body = await readJsonBody(request);
      const agentName = decodeURIComponent(match[1]!);
      const id = readOptionalString(body.id) ?? "default";
      const payload = "payload" in body ? body.payload : {};
      const envelope = await invokeNodeAgentAppHandler({
        ...options,
        agentName,
        id,
        payload,
      });
      await writeJson(response, 200, envelope);
      return;
    }

    await writeJson(response, 404, {
      ok: false,
      error: {
        code: "AUTOCTX_AGENT_NOT_FOUND",
        message: `No Node agent app route for ${request.method ?? "GET"} ${url.pathname}`,
      },
    });
  } catch (error) {
    await writeJson(response, 500, renderNodeAgentAppError(error));
  }
}

async function invokeNodeAgentAppHandler(
  options: NodeAgentAppServerOptions & {
    projectRoot: string;
    workspace: RuntimeWorkspaceEnv;
    agentRuntime: NodeAgentRuntimeContracts;
    env: Record<string, string>;
    agentName: string;
    id: string;
    payload: unknown;
  },
): Promise<NodeAgentAppSuccessEnvelope> {
  let runtimeHandle: NormalizedRuntimeHandle | undefined;
  try {
    const entry = await resolveAgentEntry(options.projectRoot, options.agentName, options.agentRuntime);
    const loaded = await options.agentRuntime.loadAutoctxAgent<unknown, unknown>(entry);
    runtimeHandle = createLazyRuntimeHandle(options.createRuntime, {
      agentName: loaded.name,
      id: options.id,
      env: options.env,
    });
    const result = await options.agentRuntime.invokeAutoctxAgent(loaded, {
      id: options.id,
      payload: options.payload,
      env: options.env,
      workspace: options.workspace,
      runtime: runtimeHandle?.runtime ?? options.runtime,
      eventStore: options.eventStore,
      eventSink: options.eventSink,
    });
    return {
      ok: true,
      agent: loaded.name,
      id: options.id,
      result,
    };
  } finally {
    await closeRuntimeHandle(runtimeHandle);
  }
}

async function buildManifest(
  projectRoot: string,
  agentRuntime: NodeAgentRuntimeContracts,
): Promise<{
  ok: true;
  target: "node";
  agents: Array<{
    name: string;
    relativePath: string;
    extension: string;
    triggers?: Record<string, unknown>;
  }>;
}> {
  const entries = await agentRuntime.discoverAutoctxAgents({ cwd: projectRoot });
  const agents = [];
  for (const entry of entries) {
    const loaded = await agentRuntime.loadAutoctxAgent(entry);
    agents.push({
      name: entry.name,
      relativePath: entry.relativePath,
      extension: entry.extension,
      triggers: loaded.triggers,
    });
  }
  return { ok: true, target: "node", agents };
}

async function resolveAgentEntry(
  projectRoot: string,
  agentName: string,
  agentRuntime: NodeAgentRuntimeContracts,
) {
  const agents = await agentRuntime.discoverAutoctxAgents({ cwd: projectRoot });
  const entry = agents.find((agent) => agent.name === agentName);
  if (entry) return entry;
  const available = agents.map((agent) => agent.name).join(", ");
  throw new Error(
    available
      ? `AutoContext agent not found: ${agentName}. Available: ${available}`
      : `AutoContext agent not found: ${agentName}. No handlers found under .autoctx/agents`,
  );
}

async function resolveExplicitEnv(
  projectRoot: string,
  options: NodeAgentAppServerOptions,
): Promise<Record<string, string>> {
  const envPath = normalizeOptionalString(options.envFile);
  const fileEnv = envPath
    ? await loadNodeAgentAppEnvFile(path.resolve(projectRoot, envPath), options.processEnv ?? {})
    : {};
  return {
    ...fileEnv,
    ...definedStringRecord(options.env ?? {}),
  };
}

function definedStringRecord(values: Record<string, string | undefined>): Record<string, string> {
  const result: Record<string, string> = {};
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined) result[key] = value;
  }
  return result;
}

async function createEventStore(
  projectRoot: string,
  sessionDbPath: string | undefined,
): Promise<RuntimeSessionEventStore | undefined> {
  const normalized = normalizeOptionalString(sessionDbPath);
  if (!normalized) return undefined;
  const { RuntimeSessionEventStore } = await import("../../session/runtime-events.js");
  return new RuntimeSessionEventStore(path.resolve(projectRoot, normalized));
}

function parseEnvFile(content: string): Record<string, string> {
  const env: Record<string, string> = {};
  for (const rawLine of content.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const normalized = line.startsWith("export ") ? line.slice("export ".length).trim() : line;
    const equals = normalized.indexOf("=");
    if (equals <= 0) continue;
    const key = normalized.slice(0, equals).trim();
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) continue;
    env[key] = parseEnvValue(normalized.slice(equals + 1).trim());
  }
  return env;
}

function parseEnvValue(value: string): string {
  if (
    (value.startsWith("\"") && value.endsWith("\"")) ||
    (value.startsWith("'") && value.endsWith("'"))
  ) {
    return value.slice(1, -1).replace(/\\n/g, "\n").replace(/\\"/g, "\"");
  }
  const commentStart = value.search(/\s#/);
  return (commentStart === -1 ? value : value.slice(0, commentStart)).trim();
}

async function readJsonBody(request: IncomingMessage): Promise<Record<string, unknown>> {
  const chunks: Buffer[] = [];
  let total = 0;
  for await (const chunk of request) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    total += buffer.byteLength;
    if (total > 1_000_000) {
      throw new Error("Request body is too large");
    }
    chunks.push(buffer);
  }
  if (chunks.length === 0) return {};
  const text = Buffer.concat(chunks).toString("utf-8");
  try {
    const parsed: unknown = JSON.parse(text);
    if (!isRecord(parsed)) {
      throw new Error("body must be a JSON object");
    }
    return parsed;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(`Request body must be valid JSON: ${message}`);
  }
}

function renderNodeAgentAppError(error: unknown): NodeAgentAppErrorEnvelope {
  const message = error instanceof Error ? error.message : String(error);
  return {
    ok: false,
    error: {
      code: "AUTOCTX_NODE_AGENT_APP_ERROR",
      message,
    },
  };
}

async function writeJson(
  response: ServerResponse,
  statusCode: number,
  body: unknown,
): Promise<void> {
  response.writeHead(statusCode, {
    "content-type": "application/json; charset=utf-8",
  });
  response.end(`${JSON.stringify(body, null, 2)}\n`);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readOptionalString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function normalizeOptionalString(value: string | undefined): string | undefined {
  const trimmed = value?.trim();
  return trimmed ? trimmed : undefined;
}

function normalizePackageName(packageName: string | undefined, projectRoot: string): string {
  const normalized = normalizeOptionalString(packageName);
  if (normalized) return normalized;
  const base = path.basename(projectRoot).toLowerCase()
    .replace(/[^a-z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return `${base || "autoctx"}-agent-app`;
}

function resolveProjectRoot(projectRoot: string | URL): string {
  if (projectRoot instanceof URL) return path.resolve(fileURLToPath(projectRoot));
  return path.resolve(projectRoot);
}

function parsePort(raw: string): number {
  const parsed = Number.parseInt(raw, 10);
  if (!Number.isInteger(parsed) || parsed <= 0 || parsed > 65535) {
    throw new Error("AUTOCTX_AGENT_PORT must be a TCP port between 1 and 65535");
  }
  return parsed;
}

type NormalizedRuntimeHandle = { runtime: AgentRuntime; close?: () => void | Promise<void> };

function normalizeRuntimeHandle(
  handle: NodeAgentAppRuntimeHandle | undefined,
): NormalizedRuntimeHandle | undefined {
  if (!handle) return undefined;
  if ("runtime" in handle) return handle;
  return { runtime: handle, close: handle.close?.bind(handle) };
}

function createLazyRuntimeHandle(
  factory: NodeAgentAppRuntimeFactory | undefined,
  plan: NodeAgentAppRuntimeFactoryPlan,
): NormalizedRuntimeHandle | undefined {
  if (!factory) return undefined;
  const runtime = new LazyNodeAgentAppRuntime(factory, plan);
  return {
    runtime,
    close: () => runtime.closeResolvedRuntime(),
  };
}

class LazyNodeAgentAppRuntime implements AgentRuntime {
  readonly #factory: NodeAgentAppRuntimeFactory;
  readonly #plan: NodeAgentAppRuntimeFactoryPlan;
  #handlePromise?: Promise<NormalizedRuntimeHandle | undefined>;
  #handle?: NormalizedRuntimeHandle;
  #closed = false;

  constructor(factory: NodeAgentAppRuntimeFactory, plan: NodeAgentAppRuntimeFactoryPlan) {
    this.#factory = factory;
    this.#plan = plan;
  }

  get name(): string {
    return this.#handle?.runtime.name ?? "autoctx-node-agent-app-runtime";
  }

  get supportsConcurrentRequests(): boolean | undefined {
    return this.#handle?.runtime.supportsConcurrentRequests;
  }

  async generate(opts: {
    prompt: string;
    system?: string;
    schema?: Record<string, unknown>;
  }) {
    return await (await this.#resolveRuntime()).generate(opts);
  }

  async revise(opts: {
    prompt: string;
    previousOutput: string;
    feedback: string;
    system?: string;
  }) {
    return await (await this.#resolveRuntime()).revise(opts);
  }

  close(): void {
    this.#closed = true;
    void this.closeResolvedRuntime();
  }

  async closeResolvedRuntime(): Promise<void> {
    this.#closed = true;
    if (!this.#handlePromise) return;
    let handle: NormalizedRuntimeHandle | undefined;
    try {
      handle = await this.#handlePromise;
    } catch {
      return;
    }
    await closeRuntimeHandle(handle);
  }

  async #resolveRuntime(): Promise<AgentRuntime> {
    if (this.#closed) {
      throw new Error("AutoContext Node agent app runtime is closed");
    }
    const handle = await this.#resolveHandle();
    if (!handle) {
      throw new Error("AutoContext Node agent app runtime is not configured");
    }
    return handle.runtime;
  }

  async #resolveHandle(): Promise<NormalizedRuntimeHandle | undefined> {
    if (!this.#handlePromise) {
      this.#handlePromise = Promise.resolve(this.#factory(this.#plan)).then((handle) => {
        const normalized = normalizeRuntimeHandle(handle);
        this.#handle = normalized;
        return normalized;
      });
    }
    return await this.#handlePromise;
  }
}

async function closeRuntimeHandle(handle: NormalizedRuntimeHandle | undefined): Promise<void> {
  if (!handle?.close) return;
  await handle.close();
}

export async function loadNodeAgentAppRuntimeFactory(
  moduleSpecifier: string,
  projectRoot = process.cwd(),
): Promise<NodeAgentAppRuntimeFactory> {
  const imported = await import(runtimeModuleUrl(moduleSpecifier, projectRoot));
  const factory = readRuntimeFactory(imported);
  if (!factory) {
    throw new Error(
      "AUTOCTX_RUNTIME_MODULE must export createAutoctxAgentRuntime, createRuntime, or a default function",
    );
  }
  return factory;
}

function runtimeModuleUrl(moduleSpecifier: string, projectRoot: string): string {
  if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(moduleSpecifier)) return moduleSpecifier;
  if (isFileLikeModuleSpecifier(moduleSpecifier)) {
    return pathToFileURL(path.resolve(projectRoot, moduleSpecifier)).href;
  }
  return resolveBareModuleUrl(moduleSpecifier, projectRoot) ?? moduleSpecifier;
}

function isFileLikeModuleSpecifier(moduleSpecifier: string): boolean {
  return moduleSpecifier.startsWith(".") || path.isAbsolute(moduleSpecifier);
}

function resolveBareModuleUrl(moduleSpecifier: string, projectRoot: string): string | undefined {
  try {
    const requireFromProject = createRequire(path.join(projectRoot, "package.json"));
    return pathToFileURL(requireFromProject.resolve(moduleSpecifier)).href;
  } catch {
    return undefined;
  }
}

function toPosixPath(value: string): string {
  return value.split(path.sep).join("/");
}

function readRuntimeFactory(imported: Record<string, unknown>): NodeAgentAppRuntimeFactory | undefined {
  const candidate = imported.createAutoctxAgentRuntime ?? imported.createRuntime ?? imported.default;
  return typeof candidate === "function" ? candidate as NodeAgentAppRuntimeFactory : undefined;
}
