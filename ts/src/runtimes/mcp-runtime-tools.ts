import { Buffer } from "node:buffer";

import {
  assertPublicHttpUrl,
  createSafeOutboundFetch,
  type OutboundFetch,
  type OutboundHostResolver,
} from "../security/outbound-url-policy.js";
import { registerRuntimeToolGrantSecrets } from "./workspace-env.js";
import type { RuntimeEffectDeclaration } from "./effect-policy.js";
import type {
  RuntimeGrantProvenance,
  RuntimeGrantScopePolicy,
  RuntimeToolCallContext,
  RuntimeToolCallResult,
  RuntimeToolGrant,
} from "./workspace-env.js";

export interface ConnectMcpRuntimeToolsOptions {
  url: string | URL;
  headers?: Record<string, string>;
  namePrefix?: string;
  provenance?: RuntimeGrantProvenance;
  scope?: RuntimeGrantScopePolicy;
  signal?: AbortSignal;
  timeoutMs?: number;
  clientName?: string;
  clientVersion?: string;
  clientFactory?: McpRuntimeToolClientFactory;
  fetch?: OutboundFetch;
  resolveHostname?: OutboundHostResolver;
  maxRedirects?: number;
  effect?: RuntimeEffectDeclaration;
}

export interface McpRuntimeToolClientFactoryInput {
  url: URL;
  headers: Record<string, string>;
  signal?: AbortSignal;
  timeoutMs?: number;
}

export type McpRuntimeToolClientFactory = (
  input: McpRuntimeToolClientFactoryInput,
) => Promise<McpRuntimeToolClient> | McpRuntimeToolClient;

export interface McpRuntimeToolRequestOptions {
  signal?: AbortSignal;
  timeout?: number;
}

export interface McpRuntimeToolClient {
  listTools(
    params?: { cursor?: string },
    options?: McpRuntimeToolRequestOptions,
  ): Promise<McpListToolsResult>;
  callTool(
    params: { name: string; arguments?: Record<string, unknown> },
    options?: McpRuntimeToolRequestOptions,
  ): Promise<McpToolCallResponse>;
  close(): Promise<void> | void;
}

export interface McpListToolsResult {
  tools: McpToolDescription[];
  nextCursor?: string;
}

export interface McpToolDescription {
  name: string;
  description?: string;
  inputSchema: Record<string, unknown>;
}

export interface McpToolCallResponse {
  content?: McpToolContent[];
  structuredContent?: Record<string, unknown>;
  isError?: boolean;
  toolResult?: unknown;
}

export type McpToolContent =
  | { type: "text"; text: string }
  | { type: "image"; data: string; mimeType: string }
  | { type: "audio"; data: string; mimeType: string }
  | { type: "resource"; resource: McpEmbeddedResource }
  | { type: "resource_link"; uri: string; name: string; mimeType?: string; description?: string }
  | Record<string, unknown>;

export type McpEmbeddedResource =
  | { uri: string; text: string; mimeType?: string }
  | { uri: string; blob: string; mimeType?: string };

interface McpSdkClientLike {
  listTools(
    params?: { cursor?: string },
    options?: McpRuntimeToolRequestOptions,
  ): Promise<McpListToolsResult>;
  callTool(
    params: { name: string; arguments?: Record<string, unknown> },
    resultSchema?: unknown,
    options?: McpRuntimeToolRequestOptions,
  ): Promise<unknown>;
  close(): Promise<void>;
}

const DEFAULT_MCP_TOOL_DISCOVERY_TIMEOUT_MS = 30_000;
const MAX_MCP_TOOL_DISCOVERY_TIMEOUT_MS = 10 * 60_000;
const MAX_MCP_TOOL_DISCOVERY_PAGES = 32;
const MAX_MCP_TOOL_DISCOVERY_TOOLS = 512;
const MAX_MCP_TOOL_DISCOVERY_BYTES = 2 * 1024 * 1024;
const MAX_MCP_TOOL_NAME_BYTES = 256;
const MAX_MCP_TOOL_DESCRIPTION_BYTES = 16 * 1024;
const MAX_MCP_TOOL_SCHEMA_BYTES = 64 * 1024;
const MAX_MCP_CURSOR_BYTES = 4 * 1024;

export async function connectMcpRuntimeTools(
  options: ConnectMcpRuntimeToolsOptions,
): Promise<McpRuntimeToolSet> {
  const url = assertPublicHttpUrl(options.url);
  const headers = { ...(options.headers ?? {}) };
  const client = options.clientFactory
    ? await options.clientFactory({
        url,
        headers,
        signal: options.signal,
        timeoutMs: options.timeoutMs,
      })
    : await createStreamableHttpMcpRuntimeToolClient({
        url,
        headers,
        signal: options.signal,
        timeoutMs: options.timeoutMs,
        clientName: options.clientName,
        clientVersion: options.clientVersion,
        fetch: options.fetch,
        resolveHostname: options.resolveHostname,
        maxRedirects: options.maxRedirects,
      });
  let tools: McpToolDescription[];
  try {
    tools = await listAllMcpTools(client, {
      signal: options.signal,
      timeoutMs: options.timeoutMs,
    });
  } catch (error) {
    await closeQuietly(client);
    throw error;
  }
  return new McpRuntimeToolSet({
    url,
    client,
    tools,
    namePrefix: options.namePrefix,
    provenance: options.provenance,
    scope: options.scope,
    trustedSecrets: trustedHeaderSecrets(headers),
    effect: options.effect,
  });
}

export class McpRuntimeToolSet {
  readonly tools: readonly RuntimeToolGrant[];
  readonly url: URL;

  #client: McpRuntimeToolClient;
  #closed = false;
  #originalByRuntimeName = new Map<string, string>();

  constructor(options: {
    url: URL;
    client: McpRuntimeToolClient;
    tools: readonly McpToolDescription[];
    namePrefix?: string;
    provenance?: RuntimeGrantProvenance;
    scope?: RuntimeGrantScopePolicy;
    trustedSecrets?: string[];
    effect?: RuntimeEffectDeclaration;
  }) {
    this.url = options.url;
    this.#client = options.client;
    this.tools = this.#defineRuntimeTools(options);
  }

  originalNameFor(runtimeToolName: string): string | undefined {
    return this.#originalByRuntimeName.get(runtimeToolName);
  }

  async callTool(
    runtimeToolName: string,
    args: Record<string, unknown> = {},
    context: RuntimeToolCallContext = {},
  ): Promise<RuntimeToolCallResult> {
    if (this.#closed) {
      throw new Error("MCP runtime tool set is closed");
    }
    const remoteName = this.#originalByRuntimeName.get(runtimeToolName);
    if (!remoteName) {
      throw new Error(`Unknown MCP runtime tool: ${runtimeToolName}`);
    }
    const response = await this.#client.callTool(
      { name: remoteName, arguments: args },
      requestOptionsFromRuntime(context),
    );
    return mcpToolCallResponseToRuntimeResult(response);
  }

  async close(): Promise<void> {
    if (this.#closed) return;
    this.#closed = true;
    await this.#client.close();
  }

  #defineRuntimeTools(options: {
    url: URL;
    tools: readonly McpToolDescription[];
    namePrefix?: string;
    provenance?: RuntimeGrantProvenance;
    scope?: RuntimeGrantScopePolicy;
    trustedSecrets?: string[];
    effect?: RuntimeEffectDeclaration;
  }): RuntimeToolGrant[] {
    const names = uniqueRuntimeToolNames(options.tools, options.namePrefix);
    return options.tools.map((tool, index) => {
      const name = names[index]!;
      this.#originalByRuntimeName.set(name, tool.name);
      const runtimeTool: RuntimeToolGrant = {
        kind: "tool",
        name,
        description: tool.description,
        inputSchema: copyRecord(tool.inputSchema),
        execute: (args, context) => this.callTool(name, args, context),
        provenance: {
          ...options.provenance,
          source: `mcp:${publicMcpUrl(options.url)}`,
          description: options.provenance?.description ?? `Remote MCP tool ${tool.name}`,
        },
        scope: options.scope,
        effect: options.effect,
      };
      return registerRuntimeToolGrantSecrets(runtimeTool, options.trustedSecrets ?? []);
    });
  }
}

export function normalizeMcpRuntimeToolName(name: string): string {
  const normalized = name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_]+/g, "_")
    .replace(/_+/g, "_")
    .replace(/^_+|_+$/g, "");
  const fallback = normalized || "tool";
  return /^[a-z_]/.test(fallback) ? fallback : `tool_${fallback}`;
}

export function mcpToolCallResponseToRuntimeResult(
  response: McpToolCallResponse,
): RuntimeToolCallResult {
  const parts: string[] = [];
  if ("toolResult" in response && response.toolResult !== undefined) {
    parts.push(safeJsonOrString(response.toolResult));
  }
  for (const item of response.content ?? []) {
    parts.push(mcpContentToText(item));
  }
  if (response.structuredContent !== undefined) {
    parts.push(`structuredContent:\n${safeJsonOrString(response.structuredContent, 2)}`);
  }
  return {
    text: parts.filter((part) => part.length > 0).join("\n\n"),
    isError: response.isError === true,
    content: response.content,
    structuredContent: response.structuredContent,
  };
}

async function createStreamableHttpMcpRuntimeToolClient(options: {
  url: URL;
  headers: Record<string, string>;
  signal?: AbortSignal;
  timeoutMs?: number;
  clientName?: string;
  clientVersion?: string;
  fetch?: OutboundFetch;
  resolveHostname?: OutboundHostResolver;
  maxRedirects?: number;
}): Promise<McpRuntimeToolClient> {
  const [{ Client }, { StreamableHTTPClientTransport }] = await Promise.all([
    import("@modelcontextprotocol/sdk/client/index.js"),
    import("@modelcontextprotocol/sdk/client/streamableHttp.js"),
  ]);
  const safeFetch = createSafeOutboundFetch({
    fetch: options.fetch,
    resolveHostname: options.resolveHostname,
    maxRedirects: options.maxRedirects,
    requestTimeoutMs: options.timeoutMs,
    maxResponseBytes: MAX_MCP_TOOL_DISCOVERY_BYTES,
    allowedResponseContentTypes: [
      "application/json",
      "application/*+json",
      "text/event-stream",
    ],
  });
  const transport = new StreamableHTTPClientTransport(options.url, {
    requestInit: { headers: options.headers },
    fetch: safeFetch,
  });
  const client = new Client({
    name: options.clientName ?? "autoctx-runtime-tools",
    version: options.clientVersion ?? "0.5.0",
  });
  try {
    await client.connect(transport, requestOptionsFromRuntime({
      signal: options.signal,
      timeoutMs: options.timeoutMs,
    }));
  } catch (error) {
    await safeFetch.close?.();
    throw error;
  }
  return new SdkMcpRuntimeToolClient(client, safeFetch.close);
}

class SdkMcpRuntimeToolClient implements McpRuntimeToolClient {
  #client: McpSdkClientLike;
  #closeFetch: (() => Promise<void>) | undefined;

  constructor(client: McpSdkClientLike, closeFetch?: () => Promise<void>) {
    this.#client = client;
    this.#closeFetch = closeFetch;
  }

  async listTools(
    params?: { cursor?: string },
    options?: McpRuntimeToolRequestOptions,
  ): Promise<McpListToolsResult> {
    return this.#client.listTools(params, options);
  }

  async callTool(
    params: { name: string; arguments?: Record<string, unknown> },
    options?: McpRuntimeToolRequestOptions,
  ): Promise<McpToolCallResponse> {
    const response = await this.#client.callTool(params, undefined, options);
    return response as McpToolCallResponse;
  }

  async close(): Promise<void> {
    try {
      await this.#client.close();
    } finally {
      await this.#closeFetch?.();
    }
  }
}

async function listAllMcpTools(
  client: McpRuntimeToolClient,
  context: RuntimeToolCallContext,
): Promise<McpToolDescription[]> {
  const timeoutMs = context.timeoutMs ?? DEFAULT_MCP_TOOL_DISCOVERY_TIMEOUT_MS;
  if (!Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > MAX_MCP_TOOL_DISCOVERY_TIMEOUT_MS) {
    throw new Error(
      `MCP tool discovery timeout must be an integer between 1 and ${MAX_MCP_TOOL_DISCOVERY_TIMEOUT_MS} ms`,
    );
  }
  const deadline = Date.now() + timeoutMs;
  const tools: McpToolDescription[] = [];
  const seenCursors = new Set<string>();
  let retainedBytes = 0;
  let cursor: string | undefined;
  for (let pageCount = 0; pageCount < MAX_MCP_TOOL_DISCOVERY_PAGES; pageCount += 1) {
    const remainingMs = deadline - Date.now();
    if (remainingMs < 1) {
      throw new Error(`MCP tool discovery exceeded its ${timeoutMs} ms deadline`);
    }
    const page = await awaitMcpDiscoveryPage(
      client.listTools(
        cursor ? { cursor } : undefined,
        requestOptionsFromRuntime({ ...context, timeoutMs: remainingMs }),
      ),
      remainingMs,
      context.signal,
    );
    if (!page || !Array.isArray(page.tools)) {
      throw new Error("MCP tool discovery returned an invalid tools page");
    }
    for (const tool of page.tools) {
      retainedBytes += validateMcpToolDescription(tool);
      if (retainedBytes > MAX_MCP_TOOL_DISCOVERY_BYTES) {
        throw new Error(
          `MCP tool discovery exceeded ${MAX_MCP_TOOL_DISCOVERY_BYTES} retained bytes`,
        );
      }
      tools.push(tool);
      if (tools.length > MAX_MCP_TOOL_DISCOVERY_TOOLS) {
        throw new Error(
          `MCP tool discovery exceeded ${MAX_MCP_TOOL_DISCOVERY_TOOLS} tools`,
        );
      }
    }
    const nextCursor = page.nextCursor;
    if (!nextCursor) return tools;
    if (typeof nextCursor !== "string" || Buffer.byteLength(nextCursor, "utf8") > MAX_MCP_CURSOR_BYTES) {
      throw new Error("MCP tool discovery returned an invalid pagination cursor");
    }
    if (seenCursors.has(nextCursor)) {
      throw new Error(`MCP tool discovery returned a repeated cursor: ${nextCursor}`);
    }
    seenCursors.add(nextCursor);
    cursor = nextCursor;
  }
  throw new Error(`MCP tool discovery exceeded ${MAX_MCP_TOOL_DISCOVERY_PAGES} pages`);
}

function validateMcpToolDescription(tool: McpToolDescription): number {
  if (!tool || typeof tool !== "object" || Array.isArray(tool)) {
    throw new Error("MCP tool discovery returned an invalid tool description");
  }
  if (
    typeof tool.name !== "string"
    || tool.name.length === 0
    || Buffer.byteLength(tool.name, "utf8") > MAX_MCP_TOOL_NAME_BYTES
  ) {
    throw new Error("MCP tool discovery returned an invalid tool name");
  }
  if (
    tool.description !== undefined
    && (
      typeof tool.description !== "string"
      || Buffer.byteLength(tool.description, "utf8") > MAX_MCP_TOOL_DESCRIPTION_BYTES
    )
  ) {
    throw new Error("MCP tool discovery returned an oversized tool description");
  }
  if (!tool.inputSchema || typeof tool.inputSchema !== "object" || Array.isArray(tool.inputSchema)) {
    throw new Error("MCP tool discovery returned an invalid tool input schema");
  }
  const schemaBytes = serializedUtf8Bytes(tool.inputSchema);
  if (schemaBytes > MAX_MCP_TOOL_SCHEMA_BYTES) {
    throw new Error("MCP tool discovery returned an oversized tool input schema");
  }
  return Buffer.byteLength(tool.name, "utf8")
    + Buffer.byteLength(tool.description ?? "", "utf8")
    + schemaBytes;
}

function serializedUtf8Bytes(value: unknown): number {
  let serialized: string | undefined;
  try {
    serialized = JSON.stringify(value);
  } catch {
    throw new Error("MCP tool discovery returned a non-serializable tool input schema");
  }
  if (serialized === undefined) {
    throw new Error("MCP tool discovery returned a non-serializable tool input schema");
  }
  return Buffer.byteLength(serialized, "utf8");
}

async function awaitMcpDiscoveryPage<T>(
  promise: Promise<T>,
  timeoutMs: number,
  signal?: AbortSignal,
): Promise<T> {
  if (signal?.aborted) throw signal.reason;
  let timeoutHandle: ReturnType<typeof setTimeout> | undefined;
  let abortHandler: (() => void) | undefined;
  const deadlinePromise = new Promise<never>((_resolve, reject) => {
    timeoutHandle = setTimeout(
      () => reject(new Error("MCP tool discovery exceeded its overall deadline")),
      timeoutMs,
    );
    if (signal) {
      abortHandler = () => reject(signal.reason);
      signal.addEventListener("abort", abortHandler, { once: true });
    }
  });
  try {
    return await Promise.race([promise, deadlinePromise]);
  } finally {
    if (timeoutHandle !== undefined) clearTimeout(timeoutHandle);
    if (signal && abortHandler) signal.removeEventListener("abort", abortHandler);
  }
}

function trustedHeaderSecrets(headers: Record<string, string>): string[] {
  return Object.values(headers);
}

async function closeQuietly(client: McpRuntimeToolClient): Promise<void> {
  try {
    await client.close();
  } catch {
    // Discovery failure should remain the reported failure.
  }
}

function requestOptionsFromRuntime(
  context: RuntimeToolCallContext,
): McpRuntimeToolRequestOptions | undefined {
  const options: McpRuntimeToolRequestOptions = {};
  if (context.signal) options.signal = context.signal;
  if (context.timeoutMs !== undefined) options.timeout = context.timeoutMs;
  return Object.keys(options).length > 0 ? options : undefined;
}

function uniqueRuntimeToolNames(
  tools: readonly McpToolDescription[],
  namePrefix?: string,
): string[] {
  const prefix = namePrefix ? normalizeMcpRuntimeToolName(namePrefix) : "";
  const used = new Set<string>();
  return tools.map((tool) => {
    const base = [prefix, normalizeMcpRuntimeToolName(tool.name)]
      .filter(Boolean)
      .join("_");
    let name = base;
    let suffix = 2;
    while (used.has(name)) {
      name = `${base}_${suffix}`;
      suffix += 1;
    }
    used.add(name);
    return name;
  });
}

function mcpContentToText(content: McpToolContent): string {
  if (content.type === "text" && typeof content.text === "string") {
    return content.text;
  }
  if (content.type === "image" && typeof content.data === "string") {
    return `[image ${readString(content.mimeType, "application/octet-stream")} ${base64Bytes(content.data)} bytes]`;
  }
  if (content.type === "audio" && typeof content.data === "string") {
    return `[audio ${readString(content.mimeType, "application/octet-stream")} ${base64Bytes(content.data)} bytes]`;
  }
  if (content.type === "resource" && isRecord(content.resource)) {
    return embeddedResourceToText(content.resource);
  }
  if (content.type === "resource_link") {
    const mimeType = readString(content.mimeType);
    const suffix = mimeType ? ` ${mimeType}` : "";
    return `[resource_link ${readString(content.name, "resource")} ${readString(content.uri)}${suffix}]`;
  }
  return safeJsonOrString(content);
}

function embeddedResourceToText(resource: Record<string, unknown>): string {
  const uri = readString(resource.uri);
  const mimeType = readString(resource.mimeType);
  if (typeof resource.text === "string") {
    const suffix = mimeType ? ` ${mimeType}` : "";
    return `resource ${uri}${suffix}\n${resource.text}`;
  }
  if (typeof resource.blob === "string") {
    const suffix = mimeType ? ` ${mimeType}` : "";
    return `[resource ${uri}${suffix} ${base64Bytes(resource.blob)} bytes]`;
  }
  return safeJsonOrString(resource);
}

function base64Bytes(value: string): number {
  return Buffer.from(value, "base64").byteLength;
}

function publicMcpUrl(url: URL): string {
  const copy = new URL(url.toString());
  copy.username = "";
  copy.password = "";
  copy.search = "";
  copy.hash = "";
  return copy.toString();
}

function copyRecord(value: Record<string, unknown>): Record<string, unknown> {
  return { ...value };
}

function readString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function safeJsonOrString(value: unknown, space?: number): string {
  try {
    return JSON.stringify(value, null, space) ?? String(value);
  } catch {
    try {
      return String(value);
    } catch {
      return "[unserializable]";
    }
  }
}
