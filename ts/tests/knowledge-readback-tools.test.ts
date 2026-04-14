import { describe, expect, it, vi } from "vitest";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

import { registerKnowledgeReadbackTools } from "../src/mcp/knowledge-readback-tools.js";

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

function createStoreStub() {
  return {
    getScoreTrajectory: vi.fn(() => []),
    getAgentOutputs: vi.fn(() => []),
    listRuns: vi.fn(() => []),
    getGenerations: vi.fn(() => []),
  };
}

describe("knowledge readback MCP tools", () => {
  it("renders trajectory markdown and falls back when no rows exist", async () => {
    const server = createFakeServer();
    const store = createStoreStub();
    store.getScoreTrajectory
      .mockReturnValueOnce([])
      .mockReturnValueOnce([{ generation_index: 1 }] as never);

    registerKnowledgeReadbackTools(server, {
      store,
      artifactExportStore: {} as never,
      runsRoot: "/runs",
      knowledgeRoot: "/knowledge",
      internals: {
        buildTrajectory: (rows) => (rows.length === 0 ? "" : "## Score Trajectory"),
      },
    });

    const emptyResult = await server.registeredTools.read_trajectory.handler({
      runId: "run-empty",
    });
    expect(emptyResult.content[0].text).toBe("No trajectory data.");

    const populatedResult = await server.registeredTools.read_trajectory.handler({
      runId: "run-1",
    });
    expect(populatedResult.content[0].text).toBe("## Score Trajectory");
  });

  it("reads hints and analyst output through shared store/artifact dependencies", async () => {
    const server = createFakeServer();
    const store = createStoreStub();
    store.getAgentOutputs.mockReturnValueOnce([
      { role: "competitor", content: "ignore me" },
      { role: "analyst", content: "Analyst summary" },
    ] as never);

    registerKnowledgeReadbackTools(server, {
      store,
      artifactExportStore: {} as never,
      runsRoot: "/runs",
      knowledgeRoot: "/knowledge",
      internals: {
        createArtifactStore: () => ({
          readPlaybook: () => "playbook body",
        }),
        extractDelimitedSection: vi.fn(() => "Try flanking."),
      },
    });

    const hints = await server.registeredTools.read_hints.handler({ scenario: "grid_ctf" });
    expect(hints.content[0].text).toBe("Try flanking.");

    const analysis = await server.registeredTools.read_analysis.handler({
      runId: "run-1",
      generation: 1,
    });
    expect(analysis.content[0].text).toBe("Analyst summary");
  });

  it("reads persisted tool files and skill notes from the knowledge root", async () => {
    const tempDir = mkdtempSync(join(tmpdir(), "ac-knowledge-readback-"));
    const knowledgeRoot = join(tempDir, "knowledge");
    mkdirSync(join(knowledgeRoot, "grid_ctf", "tools"), { recursive: true });
    writeFileSync(join(knowledgeRoot, "grid_ctf", "tools", "helper.py"), "print('hi')\n", "utf-8");
    writeFileSync(join(knowledgeRoot, "grid_ctf", "tools", "helper.ts"), "export const x = 1;\n", "utf-8");
    writeFileSync(join(knowledgeRoot, "grid_ctf", "tools", "ignore.txt"), "skip\n", "utf-8");
    writeFileSync(join(knowledgeRoot, "grid_ctf", "SKILL.md"), "# Grid CTF\n", "utf-8");

    const server = createFakeServer();
    registerKnowledgeReadbackTools(server, {
      store: createStoreStub(),
      artifactExportStore: {} as never,
      runsRoot: join(tempDir, "runs"),
      knowledgeRoot,
    });

    try {
      const tools = await server.registeredTools.read_tools.handler({ scenario: "grid_ctf" });
      expect(JSON.parse(tools.content[0].text)).toEqual([
        { name: "helper.py", code: "print('hi')\n" },
        { name: "helper.ts", code: "export const x = 1;\n" },
      ]);

      const skills = await server.registeredTools.read_skills.handler({ scenario: "grid_ctf" });
      expect(skills.content[0].text).toBe("# Grid CTF\n");
    } finally {
      rmSync(tempDir, { recursive: true, force: true });
    }
  });

  it("exports skill packages and lists solved scenarios", async () => {
    const tempDir = mkdtempSync(join(tmpdir(), "ac-knowledge-export-"));
    const knowledgeRoot = join(tempDir, "knowledge");
    mkdirSync(join(knowledgeRoot, "grid_ctf"), { recursive: true });
    mkdirSync(join(knowledgeRoot, "_internal"), { recursive: true });
    writeFileSync(join(knowledgeRoot, "grid_ctf", "playbook.md"), "playbook\n", "utf-8");

    const server = createFakeServer();
    registerKnowledgeReadbackTools(server, {
      store: createStoreStub(),
      artifactExportStore: {} as never,
      runsRoot: join(tempDir, "runs"),
      knowledgeRoot,
      internals: {
        exportStrategyPackage: vi.fn(() => ({
          scenario_name: "grid_ctf",
          best_score: 0.91,
        })),
      },
    });

    try {
      const exported = await server.registeredTools.export_skill.handler({ scenario: "grid_ctf" });
      expect(JSON.parse(exported.content[0].text)).toEqual({
        scenario_name: "grid_ctf",
        best_score: 0.91,
        suggested_filename: "grid-ctf-knowledge.md",
      });

      const solved = await server.registeredTools.list_solved.handler({});
      expect(JSON.parse(solved.content[0].text)).toEqual([
        { scenario: "grid_ctf", hasPlaybook: true },
      ]);
    } finally {
      rmSync(tempDir, { recursive: true, force: true });
    }
  });

  it("searches competitor strategies across runs and respects the limit", async () => {
    const server = createFakeServer();
    const store = createStoreStub();
    store.listRuns.mockReturnValue([
      { run_id: "run-1", scenario: "grid_ctf" },
      { run_id: "run-2", scenario: "othello" },
    ] as never);
    store.getGenerations
      .mockReturnValueOnce([
        { generation_index: 1, best_score: 0.9 },
        { generation_index: 2, best_score: 0.8 },
      ] as never)
      .mockReturnValueOnce([
        { generation_index: 1, best_score: 0.7 },
      ] as never);
    store.getAgentOutputs
      .mockReturnValueOnce([
        { role: "competitor", content: "Aggressive flank route" },
      ] as never)
      .mockReturnValueOnce([
        { role: "competitor", content: "Defensive shell" },
      ] as never)
      .mockReturnValueOnce([
        { role: "competitor", content: "Flank and capture corners" },
      ] as never);

    registerKnowledgeReadbackTools(server, {
      store,
      artifactExportStore: {} as never,
      runsRoot: "/runs",
      knowledgeRoot: "/knowledge",
    });

    const result = await server.registeredTools.search_strategies.handler({
      query: "flank",
      limit: 2,
    });

    expect(JSON.parse(result.content[0].text)).toEqual([
      {
        runId: "run-1",
        scenario: "grid_ctf",
        generation: 1,
        score: 0.9,
        strategy: "Aggressive flank route",
      },
      {
        runId: "run-2",
        scenario: "othello",
        generation: 1,
        score: 0.7,
        strategy: "Flank and capture corners",
      },
    ]);
  });
});
