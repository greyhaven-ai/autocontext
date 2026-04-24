import { join } from "node:path";

import type { AppSettings } from "../config/index.js";
import type { LoopController } from "../loop/controller.js";
import type { EventStreamEmitter } from "../loop/events.js";
import { GenerationRunner } from "../loop/generation-runner.js";
import type { RoleProviderBundle } from "../providers/index.js";
import { assertFamilyContract } from "../scenarios/family-interfaces.js";
import type { ScenarioInterface } from "../scenarios/game-interface.js";
import type { CustomScenarioEntry } from "../scenarios/custom-loader.js";
import { executeGeneratedScenarioEntry } from "../scenarios/codegen/executor.js";
import { executeAgentTaskSolve } from "../knowledge/agent-task-solve-execution.js";
import type { ScenarioFamilyName } from "../scenarios/families.js";
import { SCENARIO_REGISTRY } from "../scenarios/registry.js";
import { SQLiteStore } from "../storage/index.js";

export type RunStartPlan =
  | { kind: "builtin_game"; scenarioName: string }
  | {
    kind: "agent_task_custom";
    scenarioName: string;
    entry: CustomScenarioEntry;
  }
  | {
    kind: "generated_custom";
    scenarioName: string;
    entry: CustomScenarioEntry;
    family: ScenarioFamilyName;
  };

export function resolveRunStartPlan(opts: {
  scenario: string;
  builtinScenarioNames: string[];
  customScenario?: CustomScenarioEntry;
  customScenarioFamily?: ScenarioFamilyName | null;
}): RunStartPlan {
  if (opts.builtinScenarioNames.includes(opts.scenario)) {
    return { kind: "builtin_game", scenarioName: opts.scenario };
  }

  const customScenario = opts.customScenario;
  const family = opts.customScenarioFamily ?? null;
  if (!customScenario) {
    throw new Error(`Unknown scenario: ${opts.scenario}. Available: ${opts.builtinScenarioNames.join(", ")}`);
  }
  if (family === "agent_task" || customScenario.type === "agent_task") {
    return {
      kind: "agent_task_custom",
      scenarioName: opts.scenario,
      entry: customScenario,
    };
  }

  if (!customScenario.hasGeneratedSource || !family) {
    throw new Error(
      `Scenario '${opts.scenario}' is a saved custom ${customScenario.type ?? "unknown"} scenario. ` +
      "It is discoverable in the TS control plane, but /run currently supports only built-in game, saved agent-task, and generated custom scenarios.",
    );
  }

  return {
    kind: "generated_custom",
    scenarioName: opts.scenario,
    entry: customScenario,
    family,
  };
}

type ScenarioClass = new () => ScenarioInterface;

export function resolveBuiltInGameScenario(opts: {
  scenarioName: string;
  resolveScenarioClass?: (scenarioName: string) => ScenarioClass | undefined;
}): ScenarioInterface {
  const ScenarioClass = opts.resolveScenarioClass?.(opts.scenarioName)
    ?? SCENARIO_REGISTRY[opts.scenarioName];
  if (!ScenarioClass) {
    throw new Error(`Unknown scenario: ${opts.scenarioName}`);
  }

  const scenarioInstance = new ScenarioClass();
  assertFamilyContract(scenarioInstance, "game", `scenario '${opts.scenarioName}'`);
  return scenarioInstance;
}

interface StartRunStoreLike {
  migrate(migrationsDir: string): void;
  close(): void;
}

interface StartRunRunnerLike {
  run(runId: string, generations: number): Promise<unknown>;
}

export interface BuiltInGameStartRunDeps {
  resolveScenarioClass?: (scenarioName: string) => ScenarioClass | undefined;
  createStore?: (dbPath: string) => StartRunStoreLike;
  createRunner?: (opts: ConstructorParameters<typeof GenerationRunner>[0]) => StartRunRunnerLike;
}

export async function executeBuiltInGameStartRun(opts: {
  runId: string;
  scenarioName: string;
  generations: number;
  settings: AppSettings;
  providerBundle: RoleProviderBundle;
  opts: {
    dbPath: string;
    migrationsDir: string;
    runsRoot: string;
    knowledgeRoot: string;
  };
  controller: LoopController;
  events: EventStreamEmitter;
  scenario?: ScenarioInterface;
  deps?: BuiltInGameStartRunDeps;
}): Promise<void> {
  const scenarioInstance = opts.scenario ?? resolveBuiltInGameScenario({
    scenarioName: opts.scenarioName,
    resolveScenarioClass: opts.deps?.resolveScenarioClass,
  });

  const store = opts.deps?.createStore?.(opts.opts.dbPath) ?? new SQLiteStore(opts.opts.dbPath);
  store.migrate(opts.opts.migrationsDir);

  try {
    const runner = opts.deps?.createRunner?.({
      provider: opts.providerBundle.defaultProvider,
      roleProviders: opts.providerBundle.roleProviders,
      roleModels: opts.providerBundle.roleModels,
      scenario: scenarioInstance,
      store: store as SQLiteStore,
      runsRoot: opts.opts.runsRoot,
      knowledgeRoot: opts.opts.knowledgeRoot,
      matchesPerGeneration: opts.settings.matchesPerGeneration,
      maxRetries: opts.settings.maxRetries,
      minDelta: opts.settings.backpressureMinDelta,
      playbookMaxVersions: opts.settings.playbookMaxVersions,
      contextBudgetTokens: opts.settings.contextBudgetTokens,
      curatorEnabled: opts.settings.curatorEnabled,
      curatorConsolidateEveryNGens: opts.settings.curatorConsolidateEveryNGens,
      skillMaxLessons: opts.settings.skillMaxLessons,
      deadEndTrackingEnabled: opts.settings.deadEndTrackingEnabled,
      deadEndMaxEntries: opts.settings.deadEndMaxEntries,
      stagnationResetEnabled: opts.settings.stagnationResetEnabled,
      stagnationRollbackThreshold: opts.settings.stagnationRollbackThreshold,
      stagnationPlateauWindow: opts.settings.stagnationPlateauWindow,
      stagnationPlateauEpsilon: opts.settings.stagnationPlateauEpsilon,
      stagnationDistillTopLessons: opts.settings.stagnationDistillTopLessons,
      explorationMode: opts.settings.explorationMode,
      notifyWebhookUrl: opts.settings.notifyWebhookUrl,
      notifyOn: opts.settings.notifyOn,
      controller: opts.controller,
      events: opts.events,
    }) ?? new GenerationRunner({
      provider: opts.providerBundle.defaultProvider,
      roleProviders: opts.providerBundle.roleProviders,
      roleModels: opts.providerBundle.roleModels,
      scenario: scenarioInstance,
      store: store as SQLiteStore,
      runsRoot: opts.opts.runsRoot,
      knowledgeRoot: opts.opts.knowledgeRoot,
      matchesPerGeneration: opts.settings.matchesPerGeneration,
      maxRetries: opts.settings.maxRetries,
      minDelta: opts.settings.backpressureMinDelta,
      playbookMaxVersions: opts.settings.playbookMaxVersions,
      contextBudgetTokens: opts.settings.contextBudgetTokens,
      curatorEnabled: opts.settings.curatorEnabled,
      curatorConsolidateEveryNGens: opts.settings.curatorConsolidateEveryNGens,
      skillMaxLessons: opts.settings.skillMaxLessons,
      deadEndTrackingEnabled: opts.settings.deadEndTrackingEnabled,
      deadEndMaxEntries: opts.settings.deadEndMaxEntries,
      stagnationResetEnabled: opts.settings.stagnationResetEnabled,
      stagnationRollbackThreshold: opts.settings.stagnationRollbackThreshold,
      stagnationPlateauWindow: opts.settings.stagnationPlateauWindow,
      stagnationPlateauEpsilon: opts.settings.stagnationPlateauEpsilon,
      stagnationDistillTopLessons: opts.settings.stagnationDistillTopLessons,
      explorationMode: opts.settings.explorationMode,
      notifyWebhookUrl: opts.settings.notifyWebhookUrl,
      notifyOn: opts.settings.notifyOn,
      controller: opts.controller,
      events: opts.events,
    });

    await runner.run(opts.runId, opts.generations);
  } finally {
    store.close();
  }
}

export interface AgentTaskCustomStartRunDeps {
  executeAgentTaskSolve?: typeof executeAgentTaskSolve;
}

function readBestScore(result: Record<string, unknown>): number {
  const raw = result.best_score;
  return typeof raw === "number" && Number.isFinite(raw) ? raw : 0;
}

function normalizeCompletedGenerations(progress: number): number {
  return Number.isFinite(progress) ? Math.max(0, Math.floor(progress)) : 0;
}

export async function executeAgentTaskCustomStartRun(opts: {
  runId: string;
  scenarioName: string;
  entry: CustomScenarioEntry;
  generations: number;
  provider: import("../types/index.js").LLMProvider;
  controller: LoopController;
  events: EventStreamEmitter;
  deps?: AgentTaskCustomStartRunDeps;
}): Promise<void> {
  const executeTask = opts.deps?.executeAgentTaskSolve ?? executeAgentTaskSolve;

  opts.events.emit("run_started", {
    run_id: opts.runId,
    scenario: opts.scenarioName,
    target_generations: opts.generations,
    family: "agent_task",
    saved_custom: true,
  });
  await opts.controller.waitIfPaused();
  opts.events.emit("generation_started", { run_id: opts.runId, generation: 1 });

  const result = await executeTask({
    provider: opts.provider,
    created: {
      name: opts.scenarioName,
      spec: opts.entry.spec,
    },
    generations: opts.generations,
  });
  const bestScore = readBestScore(result.result);
  const completedGenerations = normalizeCompletedGenerations(result.progress);

  for (let generation = 1; generation <= completedGenerations; generation++) {
    if (generation > 1) {
      opts.events.emit("generation_started", { run_id: opts.runId, generation });
    }
    opts.events.emit("generation_completed", {
      run_id: opts.runId,
      generation,
      mean_score: bestScore,
      best_score: bestScore,
      elo: 1000,
      gate_decision: "advance",
      family: "agent_task",
      rounds_completed: completedGenerations,
    });
  }
  opts.events.emit("run_completed", {
    run_id: opts.runId,
    completed_generations: completedGenerations,
    best_score: bestScore,
    elo: 1000,
    family: "agent_task",
    saved_custom: true,
  });
}

export interface GeneratedCustomStartRunDeps {
  executeGeneratedScenarioEntry?: typeof executeGeneratedScenarioEntry;
}

function resolveEntryMaxSteps(entry: CustomScenarioEntry): number | undefined {
  const raw = entry.spec.max_steps ?? entry.spec.maxSteps;
  if (typeof raw === "number" && Number.isFinite(raw)) {
    return raw;
  }
  if (typeof raw === "string" && raw.trim()) {
    const parsed = Number(raw);
    if (!Number.isNaN(parsed)) {
      return parsed;
    }
  }
  return undefined;
}

export async function executeGeneratedCustomStartRun(opts: {
  runId: string;
  scenarioName: string;
  entry: CustomScenarioEntry;
  family: ScenarioFamilyName;
  generations: number;
  knowledgeRoot: string;
  controller: LoopController;
  events: EventStreamEmitter;
  deps?: GeneratedCustomStartRunDeps;
}): Promise<void> {
  const customDir = join(opts.knowledgeRoot, "_custom_scenarios");
  const maxSteps = resolveEntryMaxSteps(opts.entry);
  const executeScenario = opts.deps?.executeGeneratedScenarioEntry ?? executeGeneratedScenarioEntry;

  opts.events.emit("run_started", {
    run_id: opts.runId,
    scenario: opts.scenarioName,
    target_generations: opts.generations,
    family: opts.family,
    generated_custom: true,
  });

  let bestScoreOverall = 0;
  for (let generation = 1; generation <= opts.generations; generation++) {
    await opts.controller.waitIfPaused();
    opts.events.emit("generation_started", { run_id: opts.runId, generation });

    const result = await executeScenario({
      customDir,
      name: opts.scenarioName,
      family: opts.family,
      seed: generation,
      ...(typeof maxSteps === "number" ? { maxSteps } : {}),
    });

    bestScoreOverall = Math.max(bestScoreOverall, result.score);
    opts.events.emit("generation_completed", {
      run_id: opts.runId,
      generation,
      mean_score: result.score,
      best_score: result.score,
      elo: 1000,
      gate_decision: "advance",
      family: opts.family,
      steps_executed: result.stepsExecuted,
      reasoning: result.reasoning,
    });
  }

  opts.events.emit("run_completed", {
    run_id: opts.runId,
    completed_generations: opts.generations,
    best_score: bestScoreOverall,
    elo: 1000,
    family: opts.family,
    generated_custom: true,
  });
}
