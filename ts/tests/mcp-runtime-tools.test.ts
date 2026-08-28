import { describe, expect, it, vi } from "vitest";

import {
  connectMcpRuntimeTools,
  type McpRuntimeToolClient,
} from "../src/runtimes/mcp-runtime-tools.js";
import { createInMemoryWorkspaceEnv } from "../src/runtimes/workspace-env.js";
import { RuntimeSession } from "../src/session/runtime-session.js";
import { RuntimeSessionEventType } from "../src/session/runtime-events.js";

function mockClient(overrides: Partial<McpRuntimeToolClient> = {}): McpRuntimeToolClient {
  return {
    listTools: async () => ({ tools: [] }),
    callTool: async () => ({ content: [] }),
    close: async () => {},
    ...overrides,
  };
}

describe("MCP runtime tools", () => {
  it.each([
    "file:///tmp/mcp.sock",
    "http://localhost:3000/rpc",
    "http://127.0.0.1:3000/rpc",
    "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.8/rpc",
    "http://[::1]/rpc",
    "https://mcp.example.test/rpc#secret",
  ])("rejects unsafe MCP endpoints before invoking a client factory: %s", async (url) => {
    const clientFactory = vi.fn(async () => mockClient());

    await expect(connectMcpRuntimeTools({ url, clientFactory })).rejects.toThrow(/http|public|fragment/);
    expect(clientFactory).not.toHaveBeenCalled();
  });

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

  it("redacts URL query strings from tool provenance", async () => {
    const toolSet = await connectMcpRuntimeTools({
      url: "https://mcp.example.test/rpc?token=url-secret",
      clientFactory: async () =>
        mockClient({
          listTools: async () => ({
            tools: [{ name: "lookup", inputSchema: { type: "object" } }],
          }),
        }),
    });

    expect(toolSet.tools[0]!.provenance?.source).toBe("mcp:https://mcp.example.test/rpc");
    expect(JSON.stringify(toolSet.tools)).not.toContain("url-secret");
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

  it("fails closed when tool discovery repeats a pagination cursor", async () => {
    let closed = false;

    await expect(connectMcpRuntimeTools({
      url: "https://mcp.example.test/rpc",
      clientFactory: async () =>
        mockClient({
          listTools: async () => ({
            tools: [{ name: "lookup", inputSchema: { type: "object" } }],
            nextCursor: "again",
          }),
          close: async () => {
            closed = true;
          },
        }),
    })).rejects.toThrow("repeated cursor");

    expect(closed).toBe(true);
  });

  it("bounds retained tool discovery metadata", async () => {
    let closed = false;
    const tools = Array.from({ length: 129 }, (_value, index) => ({
      name: `tool_${index}`,
      description: "d".repeat(16 * 1024),
      inputSchema: { type: "object" },
    }));

    await expect(connectMcpRuntimeTools({
      url: "https://mcp.example.test/rpc",
      clientFactory: async () => mockClient({
        listTools: async () => ({ tools }),
        close: async () => {
          closed = true;
        },
      }),
    })).rejects.toThrow("retained bytes");

    expect(closed).toBe(true);
  });

  it("rejects oversized individual tool schemas", async () => {
    await expect(connectMcpRuntimeTools({
      url: "https://mcp.example.test/rpc",
      clientFactory: async () => mockClient({
        listTools: async () => ({
          tools: [{
            name: "oversized",
            inputSchema: { description: "x".repeat(64 * 1024) },
          }],
        }),
      }),
    })).rejects.toThrow("oversized tool input schema");
  });

  it("enforces one overall deadline across tool discovery", async () => {
    let closed = false;

    await expect(connectMcpRuntimeTools({
      url: "https://mcp.example.test/rpc",
      timeoutMs: 10,
      clientFactory: async () => mockClient({
        listTools: () => new Promise(() => {}),
        close: async () => {
          closed = true;
        },
      }),
    })).rejects.toThrow("overall deadline");

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

  it("records scoped MCP tool calls in runtime-session grant events", async () => {
    const toolSet = await connectMcpRuntimeTools({
      url: "https://mcp.example.test/rpc",
      headers: { Authorization: "Bearer trusted-token" },
      clientFactory: async () =>
        mockClient({
          listTools: async () => ({
            tools: [{ name: "lookup", inputSchema: { type: "object" } }],
          }),
          callTool: async () => ({
            content: [{ type: "text", text: "Bearer trusted-token" }],
          }),
        }),
    });
    const workspace = await createInMemoryWorkspaceEnv({ cwd: "/workspace" }).scope({
      tools: [...toolSet.tools],
    });
    const session = RuntimeSession.create({
      sessionId: "runtime-mcp-tools",
      goal: "audit mcp tool use",
      workspace,
    });

    const result = await session.submitPrompt({
      prompt: "Use the MCP tool",
      handler: async ({ workspace: scopedWorkspace }) => {
        const tool = scopedWorkspace.tools?.[0];
        expect(tool).toBeDefined();
        const call = await tool!.execute!({ token: "Bearer trusted-token" });
        expect(call.text).toBe("Bearer trusted-token");
        return { text: "handled" };
      },
    });

    expect(result.isError).toBe(false);
    expect(JSON.stringify(session.log.toJSON())).not.toContain("trusted-token");
    expect(session.log.events.map((event) => event.eventType)).toEqual([
      RuntimeSessionEventType.PROMPT_SUBMITTED,
      RuntimeSessionEventType.TOOL_CALL,
      RuntimeSessionEventType.TOOL_CALL,
      RuntimeSessionEventType.ASSISTANT_MESSAGE,
    ]);
    expect(session.log.events[1].payload).toMatchObject({
      phase: "start",
      toolName: "lookup",
      argsSummary: ['{"token":"[redacted]"}'],
      redaction: { envKeys: [] },
    });
    expect(session.log.events[2].payload).toMatchObject({
      phase: "end",
      toolName: "lookup",
      exitCode: 0,
      stdout: "[redacted]",
      redaction: {
        envKeys: [],
        stdout: { redacted: true, truncated: false },
      },
    });
  });
});
