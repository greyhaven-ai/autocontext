import { describe, expect, it, vi } from "vitest";

import {
  buildAgentTaskNotFoundPayload,
  registerAgentTaskPackageTools,
} from "../src/mcp/agent-task-package-tools.js";

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

describe("agent task and package MCP tools", () => {
  it("creates, lists, and retrieves agent tasks with stable not-found payloads", async () => {
    const server = createFakeServer();
    const taskStore = {
      create: vi.fn(),
      list: vi.fn(() => [{ name: "task-a", taskPrompt: "Prompt", rubric: "Rubric" }]),
      get: vi.fn()
        .mockReturnValueOnce({ name: "task-a", taskPrompt: "Prompt", rubric: "Rubric" })
        .mockReturnValueOnce(null),
    };

    registerAgentTaskPackageTools(server, {
      provider: { complete: vi.fn() } as never,
      store: {} as never,
      runsRoot: "/runs",
      knowledgeRoot: "/knowledge",
      skillsRoot: "/skills",
      internals: {
        createAgentTaskStore: () => taskStore,
      },
    });

    const created = await server.registeredTools.create_agent_task.handler({
      name: "task-a",
      taskPrompt: "Prompt",
      rubric: "Rubric",
    });
    expect(JSON.parse(created.content[0].text)).toEqual({
      name: "task-a",
      created: true,
    });

    const listed = await server.registeredTools.list_agent_tasks.handler({});
    expect(JSON.parse(listed.content[0].text)).toEqual([
      { name: "task-a", taskPrompt: "Prompt", rubric: "Rubric" },
    ]);

    const found = await server.registeredTools.get_agent_task.handler({ name: "task-a" });
    expect(JSON.parse(found.content[0].text)).toEqual({
      name: "task-a",
      taskPrompt: "Prompt",
      rubric: "Rubric",
    });

    const missing = await server.registeredTools.get_agent_task.handler({ name: "missing" });
    expect(JSON.parse(missing.content[0].text)).toEqual(
      buildAgentTaskNotFoundPayload(),
    );
  });

  it("generates output through the provider and returns output/model payloads", async () => {
    const server = createFakeServer();
    const complete = vi.fn(async () => ({ text: "generated", model: "mock-model" }));

    registerAgentTaskPackageTools(server, {
      provider: { complete } as never,
      store: {} as never,
      runsRoot: "/runs",
      knowledgeRoot: "/knowledge",
      skillsRoot: "/skills",
    });

    const result = await server.registeredTools.generate_output.handler({
      taskPrompt: "Summarize this",
      systemPrompt: "Be concise",
    });

    expect(complete).toHaveBeenCalledWith({
      systemPrompt: "Be concise",
      userPrompt: "Summarize this",
    });
    expect(JSON.parse(result.content[0].text)).toEqual({
      output: "generated",
      model: "mock-model",
    });
  });

  it("exports and imports packages through injected artifact/package workflows", async () => {
    const server = createFakeServer();
    const artifacts = { tag: "artifacts" };
    const exportPkg = vi.fn(() => ({ scenario_name: "grid_ctf", best_score: 0.91 }));
    const importPkg = vi.fn(() => ({ scenario: "grid_ctf", conflictPolicy: "merge" }));

    registerAgentTaskPackageTools(server, {
      provider: { complete: vi.fn() } as never,
      store: { tag: "store" } as never,
      runsRoot: "/runs",
      knowledgeRoot: "/knowledge",
      skillsRoot: "/skills",
      internals: {
        createArtifactStore: () => artifacts as never,
        exportStrategyPackage: exportPkg,
        importStrategyPackage: importPkg,
      },
    });

    const exported = await server.registeredTools.export_package.handler({
      scenario: "grid_ctf",
    });
    expect(exportPkg).toHaveBeenCalledWith({
      scenarioName: "grid_ctf",
      artifacts,
      store: { tag: "store" },
    });
    expect(JSON.parse(exported.content[0].text)).toEqual({
      scenario_name: "grid_ctf",
      best_score: 0.91,
    });

    const imported = await server.registeredTools.import_package.handler({
      packageData: JSON.stringify({ scenario_name: "grid_ctf" }),
      conflictPolicy: "merge",
    });
    expect(importPkg).toHaveBeenCalledWith({
      rawPackage: { scenario_name: "grid_ctf" },
      artifacts,
      skillsRoot: "/skills",
      conflictPolicy: "merge",
    });
    expect(JSON.parse(imported.content[0].text)).toEqual({
      scenario: "grid_ctf",
      conflictPolicy: "merge",
    });
  });
});
