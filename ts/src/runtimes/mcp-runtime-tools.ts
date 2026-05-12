import { Buffer } from "node:buffer";

import { registerRuntimeToolGrantSecrets } from "./workspace-env.js";
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

const MAX_MCP_TOOL_DISCOVERY_PAGES = 100;
const MAX_MCP_TOOL_DISCOVERY_TOOLS = 10_000;

export async function connectMcpRuntimeTools(
  options: ConnectMcpRuntimeToolsOptions,
): Promise<McpRuntimeToolSet> {
  const url = normalizeMcpUrl(options.url);
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
}): Promise<McpRuntimeToolClient> {
  const [{ Client }, { StreamableHTTPClientTransport }] = await Promise.all([
    import("@modelcontextprotocol/sdk/client/index.js"),
    import("@modelcontextprotocol/sdk/client/streamableHttp.js"),
  ]);
  const transport = new StreamableHTTPClientTransport(options.url, {
    requestInit: { headers: options.headers },
  });
  const client = new Client({
    name: options.clientName ?? "autoctx-runtime-tools",
    version: options.clientVersion ?? "0.5.0",
  });
  await client.connect(transport, requestOptionsFromRuntime({
    signal: options.signal,
    timeoutMs: options.timeoutMs,
  }));
  return new SdkMcpRuntimeToolClient(client);
}

class SdkMcpRuntimeToolClient implements McpRuntimeToolClient {
  #client: McpSdkClientLike;

  constructor(client: McpSdkClientLike) {
    this.#client = client;
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

  close(): Promise<void> {
    return this.#client.close();
  }
}

async function listAllMcpTools(
  client: McpRuntimeToolClient,
  context: RuntimeToolCallContext,
): Promise<McpToolDescription[]> {
  const tools: McpToolDescription[] = [];
  const seenCursors = new Set<string>();
  let cursor: string | undefined;
  for (let pageCount = 0; pageCount < MAX_MCP_TOOL_DISCOVERY_PAGES; pageCount += 1) {
    const page = await client.listTools(
      cursor ? { cursor } : undefined,
      requestOptionsFromRuntime(context),
    );
    tools.push(...page.tools);
    if (tools.length > MAX_MCP_TOOL_DISCOVERY_TOOLS) {
      throw new Error(
        `MCP tool discovery exceeded ${MAX_MCP_TOOL_DISCOVERY_TOOLS} tools`,
      );
    }
    const nextCursor = page.nextCursor;
    if (!nextCursor) return tools;
    if (seenCursors.has(nextCursor)) {
      throw new Error(`MCP tool discovery returned a repeated cursor: ${nextCursor}`);
    }
    seenCursors.add(nextCursor);
    cursor = nextCursor;
  }
  throw new Error(`MCP tool discovery exceeded ${MAX_MCP_TOOL_DISCOVERY_PAGES} pages`);
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

function normalizeMcpUrl(value: string | URL): URL {
  const url = value instanceof URL ? value : new URL(value);
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("MCP runtime tool URL must use http: or https:");
  }
  return url;
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
