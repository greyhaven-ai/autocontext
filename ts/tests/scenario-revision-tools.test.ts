import { describe, expect, it, vi } from "vitest";

import { registerScenarioRevisionTools } from "../src/mcp/scenario-revision-tools.js";

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

describe("scenario revision MCP tools", () => {
  it("revises scenarios through the revision workflow and normalizes missing errors to null", async () => {
    const server = createFakeServer();
    const reviseSpec = vi.fn(async () => ({
      original: { name: "draft" },
      revised: { name: "draft", objective: "Add verification" },
      changesApplied: true,
    }));

    registerScenarioRevisionTools(server, {
      provider: { complete: vi.fn(), defaultModel: () => "mock", name: "mock" } as never,
      internals: { reviseSpec },
    });

    const result = await server.registeredTools.revise_scenario.handler({
      currentSpec: { name: "draft" },
      feedback: "Add verification",
      family: "agent_task",
    });

    expect(reviseSpec).toHaveBeenCalledWith({
      currentSpec: { name: "draft" },
      feedback: "Add verification",
      family: "agent_task",
      provider: expect.objectContaining({ name: "mock" }),
    });
    expect(JSON.parse(result.content[0].text)).toEqual({
      changesApplied: true,
      revised: { name: "draft", objective: "Add verification" },
      error: null,
    });
  });

  it("preserves revision errors in the MCP payload", async () => {
    const server = createFakeServer();

    registerScenarioRevisionTools(server, {
      provider: { complete: vi.fn(), defaultModel: () => "mock", name: "mock" } as never,
      internals: {
        reviseSpec: vi.fn(async () => ({
          original: { name: "draft" },
          revised: { name: "draft" },
          changesApplied: false,
          error: "provider unavailable",
        })),
      },
    });

    const result = await server.registeredTools.revise_scenario.handler({
      currentSpec: { name: "draft" },
      feedback: "Try again",
      family: "agent_task",
    });

    expect(JSON.parse(result.content[0].text)).toEqual({
      changesApplied: false,
      revised: { name: "draft" },
      error: "provider unavailable",
    });
  });
});
