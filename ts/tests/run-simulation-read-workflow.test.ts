import { describe, expect, it, vi } from "vitest";
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { rmSync } from "node:fs";

import {
  executeRunSimulationReadRequest,
  loadReplayArtifactResponse,
} from "../src/server/run-simulation-read-workflow.js";

describe("run and simulation read workflow", () => {
  it("loads replay artifacts and preserves missing/invalid payload behavior", () => {
    const dir = mkdtempSync(join(tmpdir(), "ac-replay-"));
    try {
      const replayDir = join(dir, "run_1", "generations", "gen_1", "replays");
      mkdirSync(replayDir, { recursive: true });
      writeFileSync(join(replayDir, "replay.json"), JSON.stringify({ scenario: "grid_ctf" }), "utf-8");

      expect(loadReplayArtifactResponse({
        runsRoot: dir,
        runId: "run_1",
        generation: 1,
      })).toEqual({
        status: 200,
        body: { scenario: "grid_ctf" },
      });

      expect(loadReplayArtifactResponse({
        runsRoot: dir,
        runId: "run_1",
        generation: 99,
      })).toEqual({
        status: 404,
        body: { error: expect.stringContaining("No replay files found under") },
      });

      writeFileSync(join(replayDir, "broken.json"), JSON.stringify(["not", "an", "object"]), "utf-8");
      expect(loadReplayArtifactResponse({
        runsRoot: dir,
        runId: "run_1",
        generation: 1,
      })).toEqual({
        status: 500,
        body: { error: "Replay payload is not a JSON object" },
      });
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("routes run and knowledge reads through injected dependencies", () => {
    const store = {
      listRuns: vi.fn(() => [{ run_id: "run_1" }]),
      getRun: vi.fn(() => ({ run_id: "run_1" })),
      getGenerations: vi.fn(() => [{ generation: 1 }]),
      close: vi.fn(),
    };

    expect(executeRunSimulationReadRequest({
      route: "runs_list",
      runManager: {
        getRunsRoot: () => "/tmp/runs",
        getKnowledgeRoot: () => "/tmp/knowledge",
        getEnvironmentInfo: () => ({ scenarios: [{ name: "grid_ctf", description: "Capture the flag" }] }),
      },
      simulationApi: {
        listSimulations: vi.fn(),
        getSimulation: vi.fn(),
        getDashboardData: vi.fn(),
      },
      deps: {
        openStore: () => store,
        readPlaybook: vi.fn(() => "playbook"),
        loadReplayArtifactResponse: vi.fn(),
      },
    })).toEqual({
      status: 200,
      body: [{ run_id: "run_1" }],
    });
    expect(store.close).toHaveBeenCalledOnce();

    store.close.mockClear();
    expect(executeRunSimulationReadRequest({
      route: "run_status",
      runId: "run_1",
      runManager: {
        getRunsRoot: () => "/tmp/runs",
        getKnowledgeRoot: () => "/tmp/knowledge",
        getEnvironmentInfo: () => ({ scenarios: [{ name: "grid_ctf", description: "Capture the flag" }] }),
      },
      simulationApi: {
        listSimulations: vi.fn(),
        getSimulation: vi.fn(),
        getDashboardData: vi.fn(),
      },
      deps: {
        openStore: () => store,
        readPlaybook: vi.fn(() => "playbook"),
        loadReplayArtifactResponse: vi.fn(),
      },
    })).toEqual({
      status: 200,
      body: [{ generation: 1 }],
    });
    expect(store.close).toHaveBeenCalledOnce();
  });

  it("routes playbook, scenarios, and simulation reads with 404s for missing simulations", () => {
    const simulationApi = {
      listSimulations: vi.fn(() => [{ name: "live_sim" }]),
      getSimulation: vi.fn((name: string) => name === "live_sim" ? { name } : null),
      getDashboardData: vi.fn((name: string) => name === "live_sim" ? { name, overallScore: 0.82 } : null),
    };

    expect(executeRunSimulationReadRequest({
      route: "playbook",
      scenario: "grid_ctf",
      runManager: {
        getRunsRoot: () => "/tmp/runs",
        getKnowledgeRoot: () => "/tmp/knowledge",
        getEnvironmentInfo: () => ({ scenarios: [{ name: "grid_ctf", description: "Capture the flag" }] }),
      },
      simulationApi,
      deps: {
        openStore: vi.fn(),
        readPlaybook: vi.fn(() => "## Playbook"),
        loadReplayArtifactResponse: vi.fn(),
      },
    })).toEqual({
      status: 200,
      body: { scenario: "grid_ctf", content: "## Playbook" },
    });

    expect(executeRunSimulationReadRequest({
      route: "scenarios",
      runManager: {
        getRunsRoot: () => "/tmp/runs",
        getKnowledgeRoot: () => "/tmp/knowledge",
        getEnvironmentInfo: () => ({ scenarios: [{ name: "grid_ctf", description: "Capture the flag" }] }),
      },
      simulationApi,
      deps: {
        openStore: vi.fn(),
        readPlaybook: vi.fn(),
        loadReplayArtifactResponse: vi.fn(),
      },
    })).toEqual({
      status: 200,
      body: [{ name: "grid_ctf", description: "Capture the flag" }],
    });

    expect(executeRunSimulationReadRequest({
      route: "simulations_list",
      runManager: {
        getRunsRoot: () => "/tmp/runs",
        getKnowledgeRoot: () => "/tmp/knowledge",
        getEnvironmentInfo: () => ({ scenarios: [] }),
      },
      simulationApi,
      deps: {
        openStore: vi.fn(),
        readPlaybook: vi.fn(),
        loadReplayArtifactResponse: vi.fn(),
      },
    })).toEqual({
      status: 200,
      body: [{ name: "live_sim" }],
    });

    expect(executeRunSimulationReadRequest({
      route: "simulation_detail",
      simulationName: "missing",
      rawSimulationName: "missing",
      runManager: {
        getRunsRoot: () => "/tmp/runs",
        getKnowledgeRoot: () => "/tmp/knowledge",
        getEnvironmentInfo: () => ({ scenarios: [] }),
      },
      simulationApi,
      deps: {
        openStore: vi.fn(),
        readPlaybook: vi.fn(),
        loadReplayArtifactResponse: vi.fn(),
      },
    })).toEqual({
      status: 404,
      body: { error: "Simulation 'missing' not found" },
    });

    expect(executeRunSimulationReadRequest({
      route: "simulation_dashboard",
      simulationName: "live_sim",
      rawSimulationName: "live_sim",
      runManager: {
        getRunsRoot: () => "/tmp/runs",
        getKnowledgeRoot: () => "/tmp/knowledge",
        getEnvironmentInfo: () => ({ scenarios: [] }),
      },
      simulationApi,
      deps: {
        openStore: vi.fn(),
        readPlaybook: vi.fn(),
        loadReplayArtifactResponse: vi.fn(),
      },
    })).toEqual({
      status: 200,
      body: { name: "live_sim", overallScore: 0.82 },
    });
  });
});
