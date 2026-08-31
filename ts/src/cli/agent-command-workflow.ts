import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { readFileSync } from "node:fs";
import path from "node:path";

import {
  discoverAutoctxAgents,
  invokeAutoctxAgent,
  loadAutoctxAgent,
  type AutoctxAgentEntry,
} from "../agent-runtime/index.js";
import type { AgentRuntime } from "../runtimes/base.js";
import { createLocalWorkspaceEnv } from "../runtimes/workspace-env.js";
import {
  childProcessEnvWithoutControlPlaneSecrets,
  clearControlPlaneSecretsFromCurrentProcess,
} from "../security/child-process-env.js";
import {
  assertServerAllowedOriginsRequireAuthentication,
  assertSecureServerBind,
  isExplicitlyAllowedServerOrigin,
  resolveServerAllowedOrigins,
  resolveServerAuthToken,
  resolveServerCredentialsFile,
  SERVER_ALLOW_TOKENLESS_LOOPBACK_ENV,
  SERVER_ALLOWED_ORIGINS_ENV,
  SERVER_AUTH_TOKEN_ENV,
  SERVER_CREDENTIALS_FILE_ENV,
  ServerAuthenticator,
  type ServerCapability,
  type ServerCredentialConfig,
} from "../server/server-auth.js";

export const AGENT_COMMAND_HELP_TEXT = `autoctx agent — Run local programmable AutoContext agents

Usage:
  autoctx agent run <agent> --id <id> [--payload JSON] [--env FILE] [--json]
  autoctx agent dev [--port 3583] [--host 127.0.0.1] [--env FILE] [--json]
  autoctx agent build --target node [--out .autoctx/build/node] [--json]

Options:
  --id <id>             Invocation/session id for agent run
  --payload <json>      JSON payload passed to the handler (default: {})
  --env <file>          Explicit env file for handler context.env
  --cwd <dir>           Project root to discover .autoctx/agents from
  --provider <name>     Provider override for runtime-backed handlers
  --model <model>       Model override for runtime-backed handlers
  --api-key <key>       API key override for runtime-backed handlers
  --base-url <url>      Base URL override for compatible providers
  --port <port>         Dev server port (default: 3583)
  --host <host>         Dev server host (default: 127.0.0.1)
  --target <target>     Agent app build target (MVP: node)
  --out <dir>           Output directory for agent app build artifacts
  --json                Output machine-readable JSON

Dev/build server routes:
  GET  /manifest
  POST /agents/<agent>/invoke

Dev server authentication:
  Set AUTOCONTEXT_SERVER_TOKEN or AUTOCONTEXT_SERVER_CREDENTIALS_FILE.
  For deliberate local-only development without credentials, set
  AUTOCONTEXT_ALLOW_TOKENLESS_LOOPBACK=1.

Examples:
  autoctx agent run support --id ticket-123 --payload '{"message":"Please triage this."}' --env .env.local --json
  autoctx agent dev --port 3583 --env .env.local
  autoctx agent build --target node --out .autoctx/build/node`;

export interface AutoctxAgentCommandValues {
  id?: string;
  payload?: string;
  env?: string;
  cwd?: string;
  json?: boolean;
  port?: string;
  host?: string;
  provider?: string;
  model?: string;
  "api-key"?: string;
  "base-url"?: string;
  target?: string;
  out?: string;
}

export interface AutoctxAgentRunCommandPlan {
  action: "run";
  agentName: string;
  id: string;
  payload: unknown;
  envPath?: string;
  cwd?: string;
  json: boolean;
  provider?: string;
  model?: string;
  apiKey?: string;
  baseUrl?: string;
}

export interface AutoctxAgentDevCommandPlan {
  action: "dev";
  port: number;
  host: string;
  envPath?: string;
  cwd?: string;
  json: boolean;
  provider?: string;
  model?: string;
  apiKey?: string;
  baseUrl?: string;
}

export interface AutoctxAgentBuildCommandPlan {
  action: "build";
  target: "node";
  outDir?: string;
  cwd?: string;
  json: boolean;
}

export type AutoctxAgentCommandPlan =
  | AutoctxAgentRunCommandPlan
  | AutoctxAgentDevCommandPlan
  | AutoctxAgentBuildCommandPlan;

export interface AutoctxAgentCommandResult {
  stdout: string;
  stderr: string;
  exitCode: number;
}

export interface AutoctxAgentRunSuccessEnvelope {
  ok: true;
  agent: string;
  id: string;
  result: unknown;
}

export interface AutoctxAgentErrorEnvelope {
  ok: false;
  error: {
    code: string;
    message: string;
  };
}

export type AutoctxAgentRuntimeHandle =
  | AgentRuntime
  | { runtime: AgentRuntime; close?: () => void | Promise<void> };

export interface AutoctxAgentRuntimeFactoryPlan
  extends Pick<AutoctxAgentRunCommandPlan, "provider" | "model" | "apiKey" | "baseUrl"> {
  env: Readonly<Record<string, string>>;
  processEnv: Record<string, string | undefined>;
}

export type AutoctxAgentRuntimeFactory = (
  plan: AutoctxAgentRuntimeFactoryPlan,
) => AutoctxAgentRuntimeHandle | undefined | Promise<AutoctxAgentRuntimeHandle | undefined>;

export interface ExecuteAutoctxAgentRunCommandWorkflowOptions {
  plan: AutoctxAgentRunCommandPlan;
  cwd: string;
  processEnv?: Record<string, string | undefined>;
  createRuntime?: AutoctxAgentRuntimeFactory;
}

export interface ExecuteAutoctxAgentBuildCommandWorkflowOptions {
  plan: AutoctxAgentBuildCommandPlan;
  cwd: string;
}

export interface AutoctxAgentDevServerOptions {
  cwd: string;
  host?: string;
  envPath?: string;
  processEnv?: Record<string, string | undefined>;
  createRuntime?: AutoctxAgentRuntimeFactory;
  provider?: string;
  model?: string;
  apiKey?: string;
  baseUrl?: string;
  authToken?: string;
  authCredentials?: readonly ServerCredentialConfig[];
  credentialsFile?: string;
  allowedOrigins?: readonly string[];
}

export function planAutoctxAgentCommand(
  values: AutoctxAgentCommandValues,
  positionals: string[] = [],
): AutoctxAgentCommandPlan {
  const [action, name, ...extra] = positionals;
  if (action === "run") {
    if (!name) throw new Error("agent run requires an agent name");
    if (extra.length > 0) {
      throw new Error(`Unexpected agent run arguments: ${extra.join(" ")}`);
    }
    const payload = parsePayload(values.payload);
    const id = normalizeRequiredString(values.id, "--id");
    return {
      action: "run",
      agentName: name,
      id,
      payload,
      envPath: normalizeOptionalString(values.env),
      cwd: normalizeOptionalString(values.cwd),
      json: !!values.json,
      provider: normalizeOptionalString(values.provider),
      model: normalizeOptionalString(values.model),
      apiKey: normalizeOptionalString(values["api-key"]),
      baseUrl: normalizeOptionalString(values["base-url"]),
    };
  }
  if (action === "dev") {
    if (name || extra.length > 0) {
      throw new Error(`Unexpected agent dev arguments: ${[name, ...extra].filter(Boolean).join(" ")}`);
    }
    return {
      action: "dev",
      port: parsePort(values.port),
      host: normalizeOptionalString(values.host) ?? "127.0.0.1",
      envPath: normalizeOptionalString(values.env),
      cwd: normalizeOptionalString(values.cwd),
      json: !!values.json,
      provider: normalizeOptionalString(values.provider),
      model: normalizeOptionalString(values.model),
      apiKey: normalizeOptionalString(values["api-key"]),
      baseUrl: normalizeOptionalString(values["base-url"]),
    };
  }
  if (action === "build") {
    if (name || extra.length > 0) {
      throw new Error(`Unexpected agent build arguments: ${[name, ...extra].filter(Boolean).join(" ")}`);
    }
    const target = normalizeOptionalString(values.target) ?? "node";
    if (target !== "node") {
      throw new Error("agent build currently only supports --target node");
    }
    return {
      action: "build",
      target,
      outDir: normalizeOptionalString(values.out),
      cwd: normalizeOptionalString(values.cwd),
      json: !!values.json,
    };
  }
  throw new Error("agent requires a subcommand: run, dev, or build");
}

export function loadAutoctxAgentEnvFile(
  envPath: string,
  processEnv: Record<string, string | undefined> = process.env,
): Record<string, string> {
  const parsed = parseEnvFile(readFileSync(envPath, "utf-8"));
  const merged: Record<string, string> = {};
  for (const [key, value] of Object.entries(parsed)) {
    const shellValue = processEnv[key];
    merged[key] = shellValue === undefined ? value : shellValue;
  }
  return merged;
}

export async function executeAutoctxAgentRunCommandWorkflow(
  options: ExecuteAutoctxAgentRunCommandWorkflowOptions,
): Promise<AutoctxAgentCommandResult> {
  let runtimeHandle: NormalizedRuntimeHandle | undefined;
  const root = path.resolve(options.cwd, options.plan.cwd ?? ".");
  const workspace = createLocalWorkspaceEnv({ root });
  const processEnv = childProcessEnvWithoutControlPlaneSecrets(
    options.processEnv ?? process.env,
  );
  clearControlPlaneSecretsFromCurrentProcess();
  try {
    const entry = await resolveAgentEntry(root, options.plan.agentName);
    const env = withoutControlPlaneSecrets(options.plan.envPath
      ? loadAutoctxAgentEnvFile(path.resolve(root, options.plan.envPath), processEnv)
      : {});
    runtimeHandle = createLazyRuntimeHandle(options.createRuntime, {
      provider: options.plan.provider,
      model: options.plan.model,
      apiKey: options.plan.apiKey,
      baseUrl: options.plan.baseUrl,
      env,
      processEnv,
    });
    const loaded = await loadAutoctxAgent<unknown, unknown>(entry);
    const result = await invokeAutoctxAgent(loaded, {
      id: options.plan.id,
      payload: options.plan.payload,
      env,
      runtime: runtimeHandle?.runtime,
      workspace,
    });
    const envelope: AutoctxAgentRunSuccessEnvelope = {
      ok: true,
      agent: loaded.name,
      id: options.plan.id,
      result,
    };
    return {
      stdout: renderAutoctxAgentRunSuccess(envelope, options.plan.json),
      stderr: "",
      exitCode: 0,
    };
  } finally {
    await closeRuntimeHandle(runtimeHandle);
    await workspace.cleanup();
  }
}

export async function executeAutoctxAgentBuildCommandWorkflow(
  options: ExecuteAutoctxAgentBuildCommandWorkflowOptions,
): Promise<AutoctxAgentCommandResult> {
  const { buildNodeAgentAppTarget } = await import("../control-plane/agent-app-node/index.js");
  const root = path.resolve(options.cwd, options.plan.cwd ?? ".");
  const result = await buildNodeAgentAppTarget({
    cwd: root,
    outDir: options.plan.outDir,
  });
  if (options.plan.json) {
    return {
      stdout: JSON.stringify({
        ok: true,
        target: result.target,
        outputDir: result.outputDir,
        files: result.files.map((file) => file.relativePath),
        routes: result.routes,
      }, null, 2),
      stderr: "",
      exitCode: 0,
    };
  }
  return {
    stdout: [
      `Generated AutoContext Node agent app at ${result.outputDir}`,
      "Routes:",
      ...result.routes.map((route) => `  ${route}`),
      "Files:",
      ...result.files.map((file) => `  ${file.relativePath}`),
    ].join("\n"),
    stderr: "",
    exitCode: 0,
  };
}

export function renderAutoctxAgentCommandError(
  error: unknown,
  json: boolean,
): string {
  const message = error instanceof Error ? error.message : String(error);
  if (!json) return `Error: ${message}`;
  const envelope: AutoctxAgentErrorEnvelope = {
    ok: false,
    error: {
      code: "AUTOCTX_AGENT_ERROR",
      message,
    },
  };
  return JSON.stringify(envelope, null, 2);
}

export async function createAutoctxAgentDevServer(
  options: AutoctxAgentDevServerOptions,
): Promise<Server> {
  const security = consumeAgentDevServerSecurity(options);
  const runtimeOptions = { ...options };
  delete runtimeOptions.authToken;
  delete runtimeOptions.authCredentials;
  delete runtimeOptions.credentialsFile;
  const securedOptions = {
    ...runtimeOptions,
    processEnv: childProcessEnvWithoutControlPlaneSecrets(options.processEnv ?? process.env),
  };
  const server = createServer((request, response) => {
    void handleAgentDevRequest(request, response, securedOptions, security);
  });
  guardAgentServerListen(
    server,
    security.host,
    security.authenticator.authenticationRequired,
    security.allowTokenlessLoopback,
  );
  return server;
}

function parsePayload(raw: string | undefined): unknown {
  const value = normalizeOptionalString(raw);
  if (!value) return {};
  try {
    return JSON.parse(value);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(`--payload must be valid JSON: ${message}`);
  }
}

function parsePort(raw: string | undefined): number {
  const parsed = Number.parseInt(raw ?? "3583", 10);
  if (!Number.isInteger(parsed) || parsed <= 0 || parsed > 65535) {
    throw new Error("--port must be a TCP port between 1 and 65535");
  }
  return parsed;
}

function normalizeRequiredString(value: string | undefined, label: string): string {
  const normalized = normalizeOptionalString(value);
  if (!normalized) throw new Error(`agent run requires ${label} <value>`);
  return normalized;
}

function normalizeOptionalString(value: string | undefined): string | undefined {
  const trimmed = value?.trim();
  return trimmed ? trimmed : undefined;
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

async function resolveAgentEntry(root: string, agentName: string): Promise<AutoctxAgentEntry> {
  const agents = await discoverAutoctxAgents({ cwd: root });
  const entry = agents.find((agent) => agent.name === agentName);
  if (entry) return entry;
  const available = agents.map((agent) => agent.name).join(", ");
  throw new Error(
    available
      ? `AutoContext agent not found: ${agentName}. Available: ${available}`
      : `AutoContext agent not found: ${agentName}. No handlers found under .autoctx/agents`,
  );
}

function renderAutoctxAgentRunSuccess(
  envelope: AutoctxAgentRunSuccessEnvelope,
  json: boolean,
): string {
  if (json) return JSON.stringify(envelope, null, 2);
  if (typeof envelope.result === "string") return envelope.result;
  return JSON.stringify(envelope.result, null, 2);
}

type NormalizedRuntimeHandle = { runtime: AgentRuntime; close?: () => void | Promise<void> };

function normalizeRuntimeHandle(
  handle: AutoctxAgentRuntimeHandle | undefined,
): NormalizedRuntimeHandle | undefined {
  if (!handle) return undefined;
  if ("runtime" in handle) return handle;
  return { runtime: handle, close: handle.close?.bind(handle) };
}

async function closeRuntimeHandle(handle: NormalizedRuntimeHandle | undefined): Promise<void> {
  if (!handle?.close) return;
  await handle.close();
}

function createLazyRuntimeHandle(
  factory: AutoctxAgentRuntimeFactory | undefined,
  plan: AutoctxAgentRuntimeFactoryPlan,
): NormalizedRuntimeHandle | undefined {
  if (!factory) return undefined;
  const runtime = new LazyAutoctxAgentRuntime(factory, plan);
  return {
    runtime,
    close: () => runtime.closeResolvedRuntime(),
  };
}

class LazyAutoctxAgentRuntime implements AgentRuntime {
  readonly #factory: AutoctxAgentRuntimeFactory;
  readonly #plan: AutoctxAgentRuntimeFactoryPlan;
  #handlePromise?: Promise<NormalizedRuntimeHandle | undefined>;
  #handle?: NormalizedRuntimeHandle;
  #closed = false;

  constructor(factory: AutoctxAgentRuntimeFactory, plan: AutoctxAgentRuntimeFactoryPlan) {
    this.#factory = factory;
    this.#plan = plan;
  }

  get name(): string {
    return this.#handle?.runtime.name ?? "autoctx-agent-cli-runtime";
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
      throw new Error("AutoContext agent CLI runtime is closed");
    }
    const handle = await this.#resolveHandle();
    if (!handle) {
      throw new Error("AutoContext agent CLI runtime is not configured");
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

async function handleAgentDevRequest(
  request: IncomingMessage,
  response: ServerResponse,
  options: AutoctxAgentDevServerOptions,
  security: AgentDevServerSecurity,
): Promise<void> {
  try {
    const url = new URL(request.url ?? "/", "http://127.0.0.1");
    const method = request.method ?? "GET";
    const origin = request.headers.origin;
    if (
      origin !== undefined
      && !isExplicitlyAllowedServerOrigin(origin, security.allowedOrigins)
    ) {
      await writeJson(response, 403, {
        ok: false,
        error: { code: "AUTOCTX_AGENT_FORBIDDEN_ORIGIN", message: "Forbidden origin" },
      });
      return;
    }
    if (origin !== undefined) setAgentCorsHeaders(response, origin);

    const requiredCapabilities = requiredAgentRouteCapabilities(method, url.pathname);
    if (method === "OPTIONS" && isAgentRoutePath(url.pathname)) {
      response.writeHead(204);
      response.end();
      return;
    }
    if (requiredCapabilities !== null) {
      const authentication = security.authenticator.authenticateRequest({
        method,
        target: request.url ?? "/",
        origin: origin ?? "",
        capabilities: requiredCapabilities,
        authorizationHeader: request.headers.authorization,
      });
      if (!authentication.ok) {
        await writeAuthenticationError(response, authentication.status);
        return;
      }
    }

    if (method === "GET" && (url.pathname === "/manifest" || url.pathname === "/agents")) {
      await writeJson(response, 200, await buildManifest(options));
      return;
    }

    const match = /^\/agents\/([^/]+)\/invoke$/.exec(url.pathname);
    if (method === "POST" && match) {
      const body = await readJsonBody(request);
      const agentName = decodeURIComponent(match[1]!);
      const id = readOptionalString(body.id) ?? "default";
      const payload = body.payload ?? {};
      const result = await executeAutoctxAgentRunCommandWorkflow({
        cwd: options.cwd,
        processEnv: options.processEnv,
        createRuntime: options.createRuntime,
        plan: {
          action: "run",
          agentName,
          id,
          payload,
          envPath: options.envPath,
          json: true,
          provider: options.provider,
          model: options.model,
          apiKey: options.apiKey,
          baseUrl: options.baseUrl,
        },
      });
      await writeJson(response, result.exitCode === 0 ? 200 : 500, JSON.parse(result.stdout));
      return;
    }

    await writeJson(response, 404, {
      ok: false,
      error: {
        code: "AUTOCTX_AGENT_NOT_FOUND",
        message: `No agent dev route for ${method} ${url.pathname}`,
      },
    });
  } catch (error) {
    await writeJson(response, 500, JSON.parse(renderAutoctxAgentCommandError(error, true)));
  }
}

interface AgentDevServerSecurity {
  host: string;
  authenticator: ServerAuthenticator;
  allowedOrigins: ReadonlySet<string>;
  allowTokenlessLoopback: boolean;
}

function resolveAgentDevServerSecurity(
  options: AutoctxAgentDevServerOptions,
): AgentDevServerSecurity {
  const host = options.host ?? "127.0.0.1";
  const authToken = resolveServerAuthToken(
    options.authToken ?? options.processEnv?.[SERVER_AUTH_TOKEN_ENV],
  );
  const credentialsFile = options.credentialsFile
    ?? options.processEnv?.[SERVER_CREDENTIALS_FILE_ENV];
  const authenticator = new ServerAuthenticator({
    authToken,
    credentials: [
      ...resolveServerCredentialsFile(credentialsFile),
      ...(options.authCredentials ?? []),
    ],
  });
  const allowTokenlessLoopback = (
    options.processEnv ?? process.env
  )[SERVER_ALLOW_TOKENLESS_LOOPBACK_ENV] === "1";
  assertSecureServerBind(
    host,
    authenticator.authenticationRequired ? "configured" : null,
    allowTokenlessLoopback,
  );
  const allowedOrigins = resolveServerAllowedOrigins(
    options.allowedOrigins ?? parseConfiguredOrigins(
      options.processEnv?.[SERVER_ALLOWED_ORIGINS_ENV],
    ),
  );
  assertServerAllowedOriginsRequireAuthentication(
    authenticator.authenticationRequired,
    allowedOrigins,
  );
  return { host, authenticator, allowedOrigins, allowTokenlessLoopback };
}

function consumeAgentDevServerSecurity(
  options: AutoctxAgentDevServerOptions,
): AgentDevServerSecurity {
  try {
    return resolveAgentDevServerSecurity(options);
  } finally {
    clearControlPlaneSecretsFromCurrentProcess();
  }
}

function parseConfiguredOrigins(value: string | undefined): readonly string[] | undefined {
  if (value === undefined) return undefined;
  if (value.trim() === "") return [];
  return value.split(",").map((origin) => origin.trim());
}

function requiredAgentRouteCapabilities(
  method: string,
  pathname: string,
): readonly ServerCapability[] | null {
  if (method === "GET" && (pathname === "/manifest" || pathname === "/agents")) {
    return ["control:read", "host:execute"];
  }
  if (method === "POST" && /^\/agents\/[^/]+\/invoke$/.test(pathname)) {
    return ["content:read", "control:operate", "host:execute"];
  }
  return null;
}

function isAgentRoutePath(pathname: string): boolean {
  return pathname === "/manifest"
    || pathname === "/agents"
    || /^\/agents\/[^/]+\/invoke$/.test(pathname);
}

function setAgentCorsHeaders(response: ServerResponse, origin: string): void {
  response.setHeader("Access-Control-Allow-Origin", origin);
  response.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  response.setHeader("Access-Control-Allow-Headers", "Authorization, Content-Type");
  response.setHeader("Vary", "Origin");
}

async function writeAuthenticationError(
  response: ServerResponse,
  status: 401 | 403,
): Promise<void> {
  if (status === 401) response.setHeader("WWW-Authenticate", 'Bearer realm="autocontext"');
  await writeJson(response, status, {
    ok: false,
    error: {
      code: status === 401 ? "AUTOCTX_AGENT_UNAUTHORIZED" : "AUTOCTX_AGENT_FORBIDDEN",
      message: status === 401 ? "Unauthorized" : "Forbidden",
    },
  });
}

function guardAgentServerListen(
  server: Server,
  configuredHost: string,
  authenticationRequired: boolean,
  allowTokenlessLoopback: boolean,
): void {
  const originalListen = server.listen;
  const guardedListen = function (this: Server, ...rawArgs: unknown[]): Server {
    const args = [...rawArgs];
    if (typeof args[0] === "number") {
      const host = typeof args[1] === "string" ? args[1] : configuredHost;
      assertSecureServerBind(
        host,
        authenticationRequired ? "configured" : null,
        allowTokenlessLoopback,
      );
      if (typeof args[1] !== "string") args.splice(1, 0, host);
    } else if (isTcpListenOptions(args[0])) {
      const host = typeof args[0].host === "string" ? args[0].host : configuredHost;
      assertSecureServerBind(
        host,
        authenticationRequired ? "configured" : null,
        allowTokenlessLoopback,
      );
      args[0] = { ...args[0], host };
    } else if (!authenticationRequired) {
      throw new Error(
        "Tokenless agent servers may listen only with an explicit TCP port and loopback host",
      );
    }
    Reflect.apply(originalListen, this, args);
    return this;
  };
  server.listen = guardedListen;
}

function isTcpListenOptions(value: unknown): value is { port: number; host?: string } {
  return isRecord(value) && typeof value.port === "number";
}

function withoutControlPlaneSecrets(
  values: Record<string, string>,
): Record<string, string> {
  const sanitized = childProcessEnvWithoutControlPlaneSecrets(values);
  const result: Record<string, string> = {};
  for (const [key, value] of Object.entries(sanitized)) {
    if (value !== undefined) result[key] = value;
  }
  return result;
}

async function buildManifest(options: AutoctxAgentDevServerOptions): Promise<{
  ok: true;
  agents: Array<{
    name: string;
    relativePath: string;
    extension: string;
    triggers?: Record<string, unknown>;
  }>;
}> {
  const entries = await discoverAutoctxAgents({ cwd: options.cwd });
  const agents = [];
  for (const entry of entries) {
    const loaded = await loadAutoctxAgent(entry);
    agents.push({
      name: entry.name,
      relativePath: entry.relativePath,
      extension: entry.extension,
      triggers: loaded.triggers,
    });
  }
  return { ok: true, agents };
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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readOptionalString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
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
