import { describe, expect, it, vi } from "vitest";

import {
  buildSandboxNotFoundPayload,
  registerSandboxTools,
} from "../src/mcp/sandbox-tools.js";

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

describe("sandbox MCP tools", () => {
  it("creates, runs, lists, reads playbooks, and destroys sandboxes", async () => {
    const server = createFakeServer();
    const manager = {
      create: vi.fn(() => ({
        sandboxId: "sb-1",
        scenarioName: "grid_ctf",
        userId: "test-user",
        status: "active",
      })),
      run: vi.fn(async () => ({ runId: "run-1", bestScore: 0.91, elo: 1112 })),
      getStatus: vi.fn(() => ({
        sandboxId: "sb-1",
        scenarioName: "grid_ctf",
        userId: "test-user",
        status: "active",
      })),
      readPlaybook: vi.fn(() => "## Strategy Updates\n"),
      list: vi.fn(() => [{ sandboxId: "sb-1" }]),
      destroy: vi.fn(() => true),
    };

    registerSandboxTools(server, {
      sandboxManager: manager,
    });

    const created = await server.registeredTools.sandbox_create.handler({
      scenario: "grid_ctf",
      userId: "test-user",
    });
    expect(JSON.parse(created.content[0].text)).toEqual({
      sandboxId: "sb-1",
      scenarioName: "grid_ctf",
      userId: "test-user",
      status: "active",
    });

    const status = await server.registeredTools.sandbox_status.handler({ sandboxId: "sb-1" });
    expect(JSON.parse(status.content[0].text)).toEqual({
      sandboxId: "sb-1",
      scenarioName: "grid_ctf",
      userId: "test-user",
      status: "active",
    });

    const listed = await server.registeredTools.sandbox_list.handler({});
    expect(JSON.parse(listed.content[0].text)).toEqual([{ sandboxId: "sb-1" }]);

    const run = await server.registeredTools.sandbox_run.handler({
      sandboxId: "sb-1",
      generations: 2,
    });
    expect(manager.run).toHaveBeenCalledWith("sb-1", 2);
    expect(JSON.parse(run.content[0].text)).toEqual({
      runId: "run-1",
      bestScore: 0.91,
      elo: 1112,
    });

    const playbook = await server.registeredTools.sandbox_playbook.handler({ sandboxId: "sb-1" });
    expect(playbook.content[0].text).toBe("## Strategy Updates\n");

    const destroyed = await server.registeredTools.sandbox_destroy.handler({ sandboxId: "sb-1" });
    expect(JSON.parse(destroyed.content[0].text)).toEqual({
      destroyed: true,
      sandboxId: "sb-1",
    });
  });

  it("returns stable not-found payloads for sandbox status", async () => {
    const server = createFakeServer();

    registerSandboxTools(server, {
      sandboxManager: {
        create: vi.fn(),
        run: vi.fn(),
        getStatus: vi.fn(() => null),
        readPlaybook: vi.fn(),
        list: vi.fn(() => []),
        destroy: vi.fn(() => false),
      },
    });

    const result = await server.registeredTools.sandbox_status.handler({
      sandboxId: "missing-sb",
    });

    expect(JSON.parse(result.content[0].text)).toEqual(
      buildSandboxNotFoundPayload("missing-sb"),
    );
  });
});
