import type { LLMProvider } from "../types/index.js";
import type { SQLiteStore } from "../storage/index.js";
import type { ScenarioFamilyName } from "../scenarios/families.js";
import { CodegenUnsupportedFamilyError } from "../scenarios/codegen/registry.js";
import { executeBuiltInGameSolve } from "./built-in-game-solve-execution.js";
import { executeAgentTaskSolve } from "./agent-task-solve-execution.js";
import { executeCodegenSolve } from "./codegen-solve-execution.js";
import {
  buildSolveScenarioDescription,
  determineSolveExecutionRoute,
  persistSolveScenarioScaffold,
  prepareSolveScenario,
  validateSolveFamilyOverride,
} from "./solve-scenario-routing.js";
import { failSolveJob, type SolveJob } from "./solve-workflow.js";

export interface SolveExecutionDeps {
  createScenarioFromDescription: (description: string) => Promise<unknown>;
  listBuiltinScenarioNames: () => Promise<string[]>;
  persistSolveScenarioScaffold: typeof persistSolveScenarioScaffold;
  prepareSolveScenario: typeof prepareSolveScenario;
  determineSolveExecutionRoute: typeof determineSolveExecutionRoute;
  executeBuiltInGameSolve: typeof executeBuiltInGameSolve;
  executeAgentTaskSolve: typeof executeAgentTaskSolve;
  executeCodegenSolve: typeof executeCodegenSolve;
  failSolveJob: typeof failSolveJob;
}

export function buildSolveJobId(): string {
  return `solve_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

export function createSolveExecutionDeps(opts: {
  provider: LLMProvider;
  store: SQLiteStore;
  runsRoot: string;
  knowledgeRoot: string;
}): SolveExecutionDeps {
  return {
    createScenarioFromDescription: async (description) => {
      const { createScenarioFromDescription } = await import("../scenarios/scenario-creator.js");
      return createScenarioFromDescription(description, opts.provider);
    },
    listBuiltinScenarioNames: async () => {
      const { SCENARIO_REGISTRY } = await import("../scenarios/registry.js");
      return Object.keys(SCENARIO_REGISTRY);
    },
    persistSolveScenarioScaffold,
    prepareSolveScenario,
    determineSolveExecutionRoute,
    executeBuiltInGameSolve,
    executeAgentTaskSolve,
    executeCodegenSolve,
    failSolveJob,
  };
}

export async function runBuiltInGameSolveJob(opts: {
  job: SolveJob;
  provider: LLMProvider;
  store: SQLiteStore;
  runsRoot: string;
  knowledgeRoot: string;
  scenarioName: string;
  generations: number;
  executeBuiltInGameSolve: typeof executeBuiltInGameSolve;
}): Promise<void> {
  opts.job.status = "running";
  const result = await opts.executeBuiltInGameSolve({
    provider: opts.provider,
    store: opts.store,
    runsRoot: opts.runsRoot,
    knowledgeRoot: opts.knowledgeRoot,
    scenarioName: opts.scenarioName,
    jobId: opts.job.jobId,
    generations: opts.generations,
  });
  opts.job.progress = result.progress;
  opts.job.status = "completed";
  opts.job.result = result.result;
}

export async function runAgentTaskSolveJob(opts: {
  job: SolveJob;
  provider: LLMProvider;
  created: { name: string; spec: Record<string, unknown> };
  generations: number;
  executeAgentTaskSolve: typeof executeAgentTaskSolve;
}): Promise<void> {
  opts.job.status = "running";
  const result = await opts.executeAgentTaskSolve({
    provider: opts.provider,
    created: opts.created,
    generations: opts.generations,
  });
  opts.job.progress = result.progress;
  opts.job.status = "completed";
  opts.job.result = result.result;
}

export async function runCodegenSolveJob(opts: {
  job: SolveJob;
  knowledgeRoot: string;
  created: { name: string; family: string; spec: Record<string, unknown> };
  family: ScenarioFamilyName;
  executeCodegenSolve: typeof executeCodegenSolve;
}): Promise<void> {
  opts.job.status = "running";
  const result = await opts.executeCodegenSolve({
    knowledgeRoot: opts.knowledgeRoot,
    created: {
      name: opts.created.name,
      family: opts.family,
      spec: opts.created.spec,
    },
  });
  opts.job.progress = result.progress;
  opts.job.status = "completed";
  opts.job.result = result.result;
}

export async function executeSolveJobWorkflow(opts: {
  job: SolveJob;
  provider: LLMProvider;
  store: SQLiteStore;
  runsRoot: string;
  knowledgeRoot: string;
  deps: SolveExecutionDeps;
}): Promise<void> {
  opts.job.status = "creating_scenario";
  try {
    const familyOverride = validateSolveFamilyOverride(opts.job.familyOverride);
    const created = await opts.deps.createScenarioFromDescription(
      buildSolveScenarioDescription(opts.job.description, familyOverride),
    );
    const prepared = opts.deps.prepareSolveScenario({
      created: created as never,
      description: opts.job.description,
      familyOverride,
    });
    opts.job.scenarioName = prepared.name;
    opts.job.family = prepared.family;

    const builtinScenarioNames = await opts.deps.listBuiltinScenarioNames();
    const route = opts.deps.determineSolveExecutionRoute(prepared, builtinScenarioNames);

    if (route === "builtin_game") {
      opts.job.family = "game";
      await runBuiltInGameSolveJob({
        job: opts.job,
        provider: opts.provider,
        store: opts.store,
        runsRoot: opts.runsRoot,
        knowledgeRoot: opts.knowledgeRoot,
        scenarioName: prepared.name,
        generations: opts.job.generations,
        executeBuiltInGameSolve: opts.deps.executeBuiltInGameSolve,
      });
      return;
    }

    const persisted = await opts.deps.persistSolveScenarioScaffold({
      created: prepared,
      knowledgeRoot: opts.knowledgeRoot,
    });
    if (!persisted.persisted) {
      throw new Error(persisted.errors.join("; ") || "Scenario materialization failed.");
    }

    if (route === "missing_game") {
      throw new Error(
        `Game scenario '${prepared.name}' not found in SCENARIO_REGISTRY. ` +
        `Built-in game scenarios: ${builtinScenarioNames.join(", ")}`,
      );
    }
    if (route === "agent_task") {
      await runAgentTaskSolveJob({
        job: opts.job,
        provider: opts.provider,
        created: prepared,
        generations: opts.job.generations,
        executeAgentTaskSolve: opts.deps.executeAgentTaskSolve,
      });
      return;
    }
    if (route === "codegen") {
      await runCodegenSolveJob({
        job: opts.job,
        knowledgeRoot: opts.knowledgeRoot,
        created: prepared,
        family: prepared.family,
        executeCodegenSolve: opts.deps.executeCodegenSolve,
      });
      return;
    }
    throw new CodegenUnsupportedFamilyError(prepared.family);
  } catch (error) {
    opts.deps.failSolveJob(opts.job, error);
  }
}
