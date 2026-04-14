import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

export type RunSimulationReadRoute =
  | "runs_list"
  | "run_status"
  | "run_replay"
  | "playbook"
  | "scenarios"
  | "simulations_list"
  | "simulation_detail"
  | "simulation_dashboard";

export interface RunReadStore {
  listRuns(): unknown;
  getRun(runId: string): unknown;
  getGenerations(runId: string): unknown;
  close(): void;
}

export interface RunSimulationReadRunManager {
  getRunsRoot(): string;
  getKnowledgeRoot(): string;
  getEnvironmentInfo(): { scenarios: unknown };
}

export interface RunSimulationApi {
  listSimulations(): unknown;
  getSimulation(name: string): unknown | null;
  getDashboardData(name: string): unknown | null;
}

export interface RunSimulationReadDeps {
  openStore: () => RunReadStore;
  readPlaybook: (scenario: string, roots: { runsRoot: string; knowledgeRoot: string }) => string | null;
  loadReplayArtifactResponse: typeof loadReplayArtifactResponse;
}

export function loadReplayArtifactResponse(opts: {
  runsRoot: string;
  runId: string;
  generation: number;
}): { status: number; body: unknown } {
  const replayDir = join(
    opts.runsRoot,
    opts.runId,
    "generations",
    `gen_${opts.generation}`,
    "replays",
  );
  if (!existsSync(replayDir)) {
    return {
      status: 404,
      body: { error: `No replay files found under ${replayDir}` },
    };
  }

  const replayFiles = readdirSync(replayDir)
    .filter((name) => name.endsWith(".json"))
    .sort();
  if (replayFiles.length === 0) {
    return {
      status: 404,
      body: { error: `No replay files found under ${replayDir}` },
    };
  }

  const payload = JSON.parse(readFileSync(join(replayDir, replayFiles[0]!), "utf-8")) as unknown;
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return {
      status: 500,
      body: { error: "Replay payload is not a JSON object" },
    };
  }

  return {
    status: 200,
    body: payload,
  };
}

export function executeRunSimulationReadRequest(opts: {
  route: RunSimulationReadRoute;
  runManager: RunSimulationReadRunManager;
  simulationApi: RunSimulationApi;
  runId?: string;
  generation?: number;
  scenario?: string;
  simulationName?: string;
  rawSimulationName?: string;
  deps: RunSimulationReadDeps;
}): { status: number; body: unknown } {
  switch (opts.route) {
    case "runs_list":
      return withStore(opts.deps.openStore, (store) => ({
        status: 200,
        body: store.listRuns(),
      }));
    case "run_status":
      return withStore(opts.deps.openStore, (store) => {
        if (!store.getRun(opts.runId!)) {
          return {
            status: 404,
            body: { error: `Run '${opts.runId}' not found` },
          };
        }
        return {
          status: 200,
          body: store.getGenerations(opts.runId!),
        };
      });
    case "run_replay":
      return opts.deps.loadReplayArtifactResponse({
        runsRoot: opts.runManager.getRunsRoot(),
        runId: opts.runId!,
        generation: opts.generation!,
      });
    case "playbook":
      return {
        status: 200,
        body: {
          scenario: opts.scenario,
          content: opts.deps.readPlaybook(opts.scenario!, {
            runsRoot: opts.runManager.getRunsRoot(),
            knowledgeRoot: opts.runManager.getKnowledgeRoot(),
          }),
        },
      };
    case "scenarios":
      return {
        status: 200,
        body: opts.runManager.getEnvironmentInfo().scenarios,
      };
    case "simulations_list":
      return {
        status: 200,
        body: opts.simulationApi.listSimulations(),
      };
    case "simulation_detail": {
      const simulation = opts.simulationApi.getSimulation(opts.simulationName!);
      if (!simulation) {
        return {
          status: 404,
          body: { error: `Simulation '${opts.rawSimulationName}' not found` },
        };
      }
      return { status: 200, body: simulation };
    }
    case "simulation_dashboard": {
      const dashboard = opts.simulationApi.getDashboardData(opts.simulationName!);
      if (!dashboard) {
        return {
          status: 404,
          body: { error: `Simulation '${opts.rawSimulationName}' not found` },
        };
      }
      return { status: 200, body: dashboard };
    }
  }
}

function withStore(
  openStore: () => RunReadStore,
  fn: (store: RunReadStore) => { status: number; body: unknown },
): { status: number; body: unknown } {
  const store = openStore();
  try {
    return fn(store);
  } finally {
    store.close();
  }
}
