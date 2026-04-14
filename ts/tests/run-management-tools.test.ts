import { describe, expect, it, vi } from "vitest";

import {
  buildGenerationNotFoundPayload,
  buildRunNotFoundPayload,
  buildRunScenarioUnknownPayload,
  registerRunManagementTools,
} from "../src/mcp/run-management-tools.js";

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

const runSettings = {
  maxRetries: 2,
  backpressureMinDelta: 0.05,
  playbookMaxVersions: 5,
  contextBudgetTokens: 4096,
  curatorEnabled: false,
  curatorConsolidateEveryNGens: 3,
  skillMaxLessons: 10,
  deadEndTrackingEnabled: true,
  deadEndMaxEntries: 8,
  stagnationResetEnabled: true,
  stagnationRollbackThreshold: 2,
  stagnationPlateauWindow: 3,
  stagnationPlateauEpsilon: 0.01,
  stagnationDistillTopLessons: 2,
  explorationMode: "balanced",
  notifyWebhookUrl: undefined,
  notifyOn: undefined,
} as const;

describe("run management MCP tools", () => {
  it("lists runs, returns run status, and reads playbooks through injected stores", async () => {
    const server = createFakeServer();
    const store = {
      listRuns: vi.fn(() => [{ id: "run-1", scenario_name: "grid_ctf" }]),
      getRun: vi.fn(() => ({ id: "run-1", status: "completed" })),
      getGenerations: vi.fn(() => [{ generation_index: 1, best_score: 0.8 }]),
      getMatchesForGeneration: vi.fn(() => []),
      getAgentOutputs: vi.fn(() => []),
    };
    const readPlaybook = vi.fn(() => "# Playbook\nHold center");

    registerRunManagementTools(server, {
      store: store as never,
      provider: { complete: vi.fn(), defaultModel: () => "mock", name: "mock" } as never,
      runsRoot: "/runs",
      knowledgeRoot: "/knowledge",
      settings: runSettings,
      internals: {
        createArtifactStore: () => ({ readPlaybook }),
      },
    });

    const listed = await server.registeredTools.list_runs.handler({
      limit: 5,
      scenario: "grid_ctf",
    });
    expect(store.listRuns).toHaveBeenCalledWith(5, "grid_ctf");
    expect(JSON.parse(listed.content[0].text)).toEqual({
      runs: [{ id: "run-1", scenario_name: "grid_ctf" }],
    });

    const status = await server.registeredTools.get_run_status.handler({ runId: "run-1" });
    expect(JSON.parse(status.content[0].text)).toEqual({
      id: "run-1",
      status: "completed",
      generations: [{ generation_index: 1, best_score: 0.8 }],
    });

    const playbook = await server.registeredTools.get_playbook.handler({ scenario: "grid_ctf" });
    expect(readPlaybook).toHaveBeenCalledWith("grid_ctf");
    expect(JSON.parse(playbook.content[0].text)).toEqual({
      scenario: "grid_ctf",
      content: "# Playbook\nHold center",
    });
  });

  it("returns stable not-found payloads for missing runs and generations", async () => {
    const server = createFakeServer();
    const store = {
      listRuns: vi.fn(() => []),
      getRun: vi.fn(() => null),
      getGenerations: vi.fn(() => []),
      getMatchesForGeneration: vi.fn(() => []),
      getAgentOutputs: vi.fn(() => []),
    };

    registerRunManagementTools(server, {
      store: store as never,
      provider: { complete: vi.fn(), defaultModel: () => "mock", name: "mock" } as never,
      runsRoot: "/runs",
      knowledgeRoot: "/knowledge",
      settings: runSettings,
    });

    const runStatus = await server.registeredTools.get_run_status.handler({ runId: "missing" });
    expect(JSON.parse(runStatus.content[0].text)).toEqual(buildRunNotFoundPayload());

    const generation = await server.registeredTools.get_generation_detail.handler({
      runId: "run-1",
      generation: 9,
    });
    expect(JSON.parse(generation.content[0].text)).toEqual(buildGenerationNotFoundPayload());
  });

  it("starts scenario runs via injected runner creation and returns started payloads", async () => {
    const server = createFakeServer();
    const run = vi.fn(() => new Promise(() => {}));
    const assertFamilyContract = vi.fn();
    class ScenarioStub {}

    registerRunManagementTools(server, {
      store: {
        listRuns: vi.fn(() => []),
        getRun: vi.fn(() => null),
        getGenerations: vi.fn(() => []),
        getMatchesForGeneration: vi.fn(() => []),
        getAgentOutputs: vi.fn(() => []),
      } as never,
      provider: { complete: vi.fn(), defaultModel: () => "mock", name: "mock" } as never,
      runsRoot: "/runs",
      knowledgeRoot: "/knowledge",
      settings: runSettings,
      internals: {
        loadScenarioRegistry: () => ({ grid_ctf: ScenarioStub as never }),
        createRunId: () => "mcp-fixed",
        assertFamilyContract,
        createRunner: vi.fn(() => ({ run })),
      },
    });

    const result = await server.registeredTools.run_scenario.handler({
      scenario: "grid_ctf",
      generations: 2,
      matchesPerGeneration: 4,
    });

    expect(assertFamilyContract).toHaveBeenCalledWith(
      expect.any(ScenarioStub),
      "game",
      "scenario 'grid_ctf'",
    );
    expect(run).toHaveBeenCalledWith("mcp-fixed", 2);
    expect(JSON.parse(result.content[0].text)).toEqual({
      runId: "mcp-fixed",
      scenario: "grid_ctf",
      generations: 2,
      status: "started",
    });
  });

  it("returns stable unknown-scenario payloads and trims generation output previews", async () => {
    const server = createFakeServer();
    const store = {
      listRuns: vi.fn(() => []),
      getRun: vi.fn(() => ({ id: "run-1" })),
      getGenerations: vi.fn(() => [{ generation_index: 2, status: "completed" }]),
      getMatchesForGeneration: vi.fn(() => [{ seed: 42, score: 0.9 }]),
      getAgentOutputs: vi.fn(() => [{ role: "challenger", content: "x".repeat(650) }]),
    };

    registerRunManagementTools(server, {
      store: store as never,
      provider: { complete: vi.fn(), defaultModel: () => "mock", name: "mock" } as never,
      runsRoot: "/runs",
      knowledgeRoot: "/knowledge",
      settings: runSettings,
      internals: {
        loadScenarioRegistry: () => ({}),
      },
    });

    const unknown = await server.registeredTools.run_scenario.handler({
      scenario: "missing",
      generations: 1,
      matchesPerGeneration: 3,
    });
    expect(JSON.parse(unknown.content[0].text)).toEqual(
      buildRunScenarioUnknownPayload("missing"),
    );

    const detail = await server.registeredTools.get_generation_detail.handler({
      runId: "run-1",
      generation: 2,
    });
    const payload = JSON.parse(detail.content[0].text);
    expect(payload.generation).toEqual({ generation_index: 2, status: "completed" });
    expect(payload.matches).toEqual([{ seed: 42, score: 0.9 }]);
    expect(payload.agentOutputs).toEqual([
      { role: "challenger", contentPreview: "x".repeat(500) },
    ]);
  });
});
