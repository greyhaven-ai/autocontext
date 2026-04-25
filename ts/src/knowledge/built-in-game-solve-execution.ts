import type { LLMProvider } from "../types/index.js";
import type { SQLiteStore } from "../storage/index.js";
import type { ScenarioInterface } from "../scenarios/game-interface.js";
import { assertFamilyContract } from "../scenarios/family-interfaces.js";
import { ArtifactStore } from "./artifact-store.js";
import { exportStrategyPackage } from "./package.js";

export interface BuiltInGameSolveExecutionResult {
  progress: number;
  result: Record<string, unknown>;
}

type ScenarioClass = new () => ScenarioInterface;

export interface BuiltInGameSolveDeps {
  resolveScenarioClass?: (scenarioName: string) => Promise<ScenarioClass | undefined> | ScenarioClass | undefined;
  createRunner?: (opts: {
    provider: LLMProvider;
    scenario: ScenarioInterface;
    store: SQLiteStore;
    runsRoot: string;
    knowledgeRoot: string;
    matchesPerGeneration: number;
    maxRetries: number;
    minDelta: number;
    generationTimeBudgetSeconds?: number | null;
  }) => { run(runId: string, generations: number): Promise<{ generationsCompleted: number }> };
  exportPackage?: (opts: {
    scenarioName: string;
    artifacts: ArtifactStore;
    store: SQLiteStore;
  }) => Record<string, unknown>;
}

async function defaultResolveScenarioClass(scenarioName: string): Promise<ScenarioClass | undefined> {
  const { SCENARIO_REGISTRY } = await import("../scenarios/registry.js");
  return SCENARIO_REGISTRY[scenarioName] as ScenarioClass | undefined;
}

async function defaultCreateRunner(opts: {
  provider: LLMProvider;
  scenario: ScenarioInterface;
  store: SQLiteStore;
  runsRoot: string;
  knowledgeRoot: string;
  matchesPerGeneration: number;
  maxRetries: number;
  minDelta: number;
  generationTimeBudgetSeconds?: number | null;
}): Promise<{ run(runId: string, generations: number): Promise<{ generationsCompleted: number }> }> {
  const { GenerationRunner } = await import("../loop/generation-runner.js");
  return new GenerationRunner(opts);
}

export async function executeBuiltInGameSolve(opts: {
  provider: LLMProvider;
  store: SQLiteStore;
  runsRoot: string;
  knowledgeRoot: string;
  scenarioName: string;
  jobId: string;
  generations: number;
  generationTimeBudgetSeconds?: number | null;
  deps?: BuiltInGameSolveDeps;
}): Promise<BuiltInGameSolveExecutionResult> {
  const ScenarioClass = await (opts.deps?.resolveScenarioClass ?? defaultResolveScenarioClass)(opts.scenarioName);
  if (!ScenarioClass) {
    throw new Error(`Game scenario '${opts.scenarioName}' not found in SCENARIO_REGISTRY`);
  }

  const scenario = new ScenarioClass();
  assertFamilyContract(scenario, "game", `scenario '${opts.scenarioName}'`);
  const runner = await (opts.deps?.createRunner ?? defaultCreateRunner)({
    provider: opts.provider,
    scenario,
    store: opts.store,
    runsRoot: opts.runsRoot,
    knowledgeRoot: opts.knowledgeRoot,
    matchesPerGeneration: 2,
    maxRetries: 0,
    minDelta: 0,
    generationTimeBudgetSeconds: opts.generationTimeBudgetSeconds,
  });

  const runId = `solve_${opts.scenarioName}_${opts.jobId}`;
  const runResult = await runner.run(runId, opts.generations);
  const artifacts = new ArtifactStore({
    runsRoot: opts.runsRoot,
    knowledgeRoot: opts.knowledgeRoot,
  });

  return {
    progress: runResult.generationsCompleted,
    result: (opts.deps?.exportPackage ?? exportStrategyPackage)({
      scenarioName: opts.scenarioName,
      artifacts,
      store: opts.store,
    }),
  };
}
