import { describe, expect, it } from "vitest";

import {
  connectMcpRuntimeTools,
  type McpRuntimeToolClient,
} from "../src/runtimes/mcp-runtime-tools.js";
import { createInMemoryWorkspaceEnv } from "../src/runtimes/workspace-env.js";

function mockClient(overrides: Partial<McpRuntimeToolClient> = {}): McpRuntimeToolClient {
  return {
    listTools: async () => ({ tools: [] }),
    callTool: async () => ({ content: [] }),
    close: async () => {},
    ...overrides,
  };
}

describe("MCP runtime tools", () => {
  it("connects with trusted headers and normalizes duplicate tool names", async () => {
    const seen: Array<{ url: string; headers: Record<string, string> }> = [];
    let closed = false;

    const toolSet = await connectMcpRuntimeTools({
      url: "https://mcp.example.test/rpc",
      headers: { Authorization: "Bearer trusted-token" },
      clientFactory: async ({ url, headers }) => {
        seen.push({ url: String(url), headers });
        return mockClient({
          listTools: async () => ({
            tools: [
              {
                name: "Search API",
                description: "Search docs",
                inputSchema: {
                  type: "object",
                  properties: { q: { type: "string" } },
                  required: ["q"],
                },
              },
              {
                name: "search-api",
                description: "Search tickets",
                inputSchema: { type: "object" },
              },
            ],
          }),
          close: async () => {
            closed = true;
          },
        });
      },
    });

    expect(seen).toEqual([
      {
        url: "https://mcp.example.test/rpc",
        headers: { Authorization: "Bearer trusted-token" },
      },
    ]);
    expect(toolSet.tools.map((tool) => tool.name)).toEqual([
      "search_api",
      "search_api_2",
    ]);
    expect(toolSet.tools[0]).toMatchObject({
      kind: "tool",
      description: "Search docs",
      inputSchema: {
        type: "object",
        properties: { q: { type: "string" } },
        required: ["q"],
      },
      provenance: {
        source: "mcp:https://mcp.example.test/rpc",
      },
    });
    expect(toolSet.originalNameFor("search_api_2")).toBe("search-api");

    await toolSet.close();
    expect(closed).toBe(true);
  });

  it("redacts URL credentials and query strings from tool provenance", async () => {
    const toolSet = await connectMcpRuntimeTools({
      url: "https://user:password@mcp.example.test/rpc?token=url-secret#frag",
      clientFactory: async () =>
        mockClient({
          listTools: async () => ({
            tools: [{ name: "lookup", inputSchema: { type: "object" } }],
          }),
        }),
    });

    expect(toolSet.tools[0]!.provenance?.source).toBe("mcp:https://mcp.example.test/rpc");
    expect(JSON.stringify(toolSet.tools)).not.toContain("url-secret");
    expect(JSON.stringify(toolSet.tools)).not.toContain("password");
  });

  it("converts MCP content and structured results into model-safe text", async () => {
    const toolSet = await connectMcpRuntimeTools({
      url: "https://mcp.example.test/rpc",
      clientFactory: async () =>
        mockClient({
          listTools: async () => ({
            tools: [{ name: "render", inputSchema: { type: "object" } }],
          }),
          callTool: async () => ({
            structuredContent: { id: 42, ok: true },
            content: [
              { type: "text", text: "Rendered report" },
              { type: "image", data: "aGVsbG8=", mimeType: "image/png" },
              {
                type: "resource",
                resource: {
                  uri: "file:///report.md",
                  mimeType: "text/markdown",
                  text: "# Report",
                },
              },
              {
                type: "resource",
                resource: {
                  uri: "file:///raw.bin",
                  mimeType: "application/octet-stream",
                  blob: "aGVsbG8=",
                },
              },
              {
                type: "resource_link",
                uri: "https://example.test/report",
                name: "report-link",
                mimeType: "text/html",
              },
            ],
          }),
        }),
    });

    const result = await toolSet.tools[0]!.execute!({ id: 42 });

    expect(result).toMatchObject({
      isError: false,
      structuredContent: { id: 42, ok: true },
    });
    expect(result.text).toContain("Rendered report");
    expect(result.text).toContain("[image image/png 5 bytes]");
    expect(result.text).toContain("resource file:///report.md text/markdown");
    expect(result.text).toContain("# Report");
    expect(result.text).toContain("[resource file:///raw.bin application/octet-stream 5 bytes]");
    expect(result.text).toContain("[resource_link report-link https://example.test/report text/html]");
    expect(result.text).toContain('"ok": true');
  });

  it("preserves MCP tool failures and propagates transport failures", async () => {
    const toolSet = await connectMcpRuntimeTools({
      url: "https://mcp.example.test/rpc",
      clientFactory: async () =>
        mockClient({
          listTools: async () => ({
            tools: [
              { name: "fails_cleanly", inputSchema: { type: "object" } },
              { name: "throws", inputSchema: { type: "object" } },
            ],
          }),
          callTool: async ({ name }) => {
            if (name === "throws") throw new Error("transport down");
            return {
              isError: true,
              content: [{ type: "text", text: "tool rejected the request" }],
            };
          },
        }),
    });

    await expect(toolSet.tools[0]!.execute!({})).resolves.toMatchObject({
      isError: true,
      text: "tool rejected the request",
    });
    await expect(toolSet.tools[1]!.execute!({})).rejects.toThrow("transport down");
  });

  it("passes abort signals and timeouts through tool calls", async () => {
    const abortController = new AbortController();
    const seenOptions: unknown[] = [];
    const toolSet = await connectMcpRuntimeTools({
      url: "https://mcp.example.test/rpc",
      clientFactory: async () =>
        mockClient({
          listTools: async () => ({
            tools: [{ name: "slow_tool", inputSchema: { type: "object" } }],
          }),
          callTool: async (_params, options) => {
            seenOptions.push(options);
            return { content: [{ type: "text", text: "done" }] };
          },
        }),
    });

    await toolSet.tools[0]!.execute!({}, {
      signal: abortController.signal,
      timeoutMs: 25,
    });

    expect(seenOptions).toEqual([
      {
        signal: abortController.signal,
        timeout: 25,
      },
    ]);
  });

  it("closes an opened client when tool discovery fails", async () => {
    let closed = false;

    await expect(connectMcpRuntimeTools({
      url: "https://mcp.example.test/rpc",
      clientFactory: async () =>
        mockClient({
          listTools: async () => {
            throw new Error("discovery failed");
          },
          close: async () => {
            closed = true;
          },
        }),
    })).rejects.toThrow("discovery failed");

    expect(closed).toBe(true);
  });

  it("scopes MCP tool grants through workspace environments", async () => {
    const toolSet = await connectMcpRuntimeTools({
      url: "https://mcp.example.test/rpc",
      scope: { inheritToChildTasks: false },
      clientFactory: async () =>
        mockClient({
          listTools: async () => ({
            tools: [{ name: "lookup", inputSchema: { type: "object" } }],
          }),
        }),
    });
    const inheritableToolSet = await connectMcpRuntimeTools({
      url: "https://mcp.example.test/rpc",
      clientFactory: async () =>
        mockClient({
          listTools: async () => ({
            tools: [{ name: "shared_lookup", inputSchema: { type: "object" } }],
          }),
        }),
    });
    const env = createInMemoryWorkspaceEnv({ cwd: "/project" });

    const scoped = await env.scope({
      tools: [...toolSet.tools, ...inheritableToolSet.tools],
    });
    const child = await scoped.scope({ grantInheritance: "child_task" });

    expect(env.tools ?? []).toEqual([]);
    expect(scoped.tools?.map((tool) => tool.name)).toEqual(["lookup", "shared_lookup"]);
    expect(child.tools?.map((tool) => tool.name)).toEqual(["shared_lookup"]);
  });
});
