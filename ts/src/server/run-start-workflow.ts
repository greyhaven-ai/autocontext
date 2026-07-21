import { join } from "node:path";

import type { AppSettings } from "../config/index.js";
import { asDbPath, asRunId } from "../domain/ids.js";
import {
  isRunStopRequestedError,
  type LoopController,
} from "../loop/controller.js";
import type { EventStreamEmitter } from "../loop/events.js";
import { GenerationRunner } from "../loop/generation-runner.js";
import { createAgentTaskPlanPublisher } from "../loop/agent-task-plan.js";
import type { RoleProviderBundle } from "../providers/index.js";
import { assertFamilyContract } from "../scenarios/family-interfaces.js";
import type { ScenarioInterface } from "../scenarios/game-interface.js";
import type { CustomScenarioEntry } from "../scenarios/custom-loader.js";
import { executeGeneratedScenarioEntry } from "../scenarios/codegen/executor.js";
import {
  executeAgentTaskSolve,
  type AgentTaskSolveProgress,
} from "../knowledge/agent-task-solve-execution.js";
import { HookEvents, initializeHookBus, type HookBus } from "../extensions/index.js";
import type { ScenarioFamilyName } from "../scenarios/families.js";
import { SCENARIO_REGISTRY } from "../scenarios/registry.js";
import { SQLiteStore } from "../storage/index.js";

const SAVED_AGENT_TASK_PLAN_STEPS = [
  { id: "prepare_context", label: "Prepare task context" },
  { id: "draft_response", label: "Draft the initial response" },
  { id: "improve_response", label: "Evaluate and refine the response" },
  { id: "finalize_result", label: "Finalize the best result" },
] as const;

const GENERATED_CUSTOM_PLAN_STEPS = [
  { id: "execute_scenario", label: "Execute scenario generations" },
  { id: "aggregate_results", label: "Aggregate generation results" },
  { id: "finalize_run", label: "Finalize the run" },
] as const;

type RuntimeTaskPlanPublisher = NonNullable<
  ReturnType<typeof createAgentTaskPlanPublisher>
>;

function createRuntimeTaskPlan(opts: {
  runId: string;
  steps: readonly { id: string; label: string; detail?: string }[];
  events: EventStreamEmitter;
}): RuntimeTaskPlanPublisher | null {
  try {
    return createAgentTaskPlanPublisher(opts);
  } catch {
    return null;
  }
}

function publishTaskPlan(
  taskPlan: RuntimeTaskPlanPublisher | null,
  action: (publisher: RuntimeTaskPlanPublisher) => boolean,
): void {
  if (!taskPlan) {
    return;
  }
  try {
    action(taskPlan);
  } catch {
    // Task-plan telemetry must never alter run results.
  }
}

function reportSavedAgentTaskProgress(
  taskPlan: RuntimeTaskPlanPublisher | null,
  progress: AgentTaskSolveProgress,
): void {
  if (!taskPlan) {
    return;
  }
  const iterativeDetail =
    progress.round === undefined
      ? "Evaluating the current response"
      : `Working through evaluation round ${progress.round}`;
  if (progress.phase === "context_preparation" && progress.status === "completed") {
    publishTaskPlan(taskPlan, (publisher) => publisher.progress({
      activeStepId: "draft_response",
      completedStepIds: ["prepare_context"],
    }));
    return;
  }
  if (progress.phase === "draft" && progress.status === "completed") {
    publishTaskPlan(taskPlan, (publisher) => publisher.progress({
      activeStepId: "improve_response",
      completedStepIds: ["prepare_context", "draft_response"],
      stepDetails: { improve_response: { detail: iterativeDetail } },
    }));
    return;
  }
  if (progress.phase === "evaluation") {
    publishTaskPlan(taskPlan, (publisher) => publisher.progress({
      activeStepId: "improve_response",
      completedStepIds: ["prepare_context", "draft_response"],
      stepDetails: { improve_response: { detail: iterativeDetail } },
    }));
    return;
  }
  if (progress.phase === "revision" && progress.status === "started") {
    publishTaskPlan(taskPlan, (publisher) => publisher.replan({
      activeStepId: "improve_response",
      completedStepIds: ["prepare_context", "draft_response"],
      summary:
        progress.round === undefined
          ? "Refining the response after evaluation."
          : `Refining the response after evaluation round ${progress.round}.`,
      stepDetails: { improve_response: { detail: iterativeDetail } },
    }));
    return;
  }
  if (progress.phase === "revision") {
    publishTaskPlan(taskPlan, (publisher) => publisher.progress({
      activeStepId: "improve_response",
      completedStepIds: ["prepare_context", "draft_response"],
      stepDetails: { improve_response: { detail: iterativeDetail } },
    }));
    return;
  }
  if (progress.phase === "finalization") {
    publishTaskPlan(taskPlan, (publisher) => publisher.progress({
      activeStepId: "finalize_result",
      completedStepIds: ["prepare_context", "draft_response", "improve_response"],
    }));
  }
}

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
    throw new Error(
      `Unknown scenario: ${opts.scenario}. Available: ${opts.builtinScenarioNames.join(", ")}`,
    );
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
  const ScenarioClass =
    opts.resolveScenarioClass?.(opts.scenarioName) ?? SCENARIO_REGISTRY[opts.scenarioName];
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
  requirePlaybookApproval?: boolean;
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
  const scenarioInstance =
    opts.scenario ??
    resolveBuiltInGameScenario({
      scenarioName: opts.scenarioName,
      resolveScenarioClass: opts.deps?.resolveScenarioClass,
    });

  const store =
    opts.deps?.createStore?.(opts.opts.dbPath) ?? new SQLiteStore(asDbPath(opts.opts.dbPath));
  store.migrate(opts.opts.migrationsDir);
  const { hookBus, loadedExtensions } = await initializeHookBus({
    extensions: opts.settings.extensions,
    failFast: opts.settings.extensionFailFast,
  });

  try {
    const runner =
      opts.deps?.createRunner?.({
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
        requirePlaybookApproval: opts.requirePlaybookApproval ?? false,
        contextBudgetTokens: opts.settings.contextBudgetTokens,
        curatorEnabled: opts.settings.curatorEnabled,
        curatorConsolidateEveryNGens: opts.settings.curatorConsolidateEveryNGens,
        softHintsEnabled: opts.settings.softHintsEnabled,
        hintStyle: opts.settings.hintStyle,
        skillMaxLessons: opts.settings.skillMaxLessons,
        deadEndTrackingEnabled: opts.settings.deadEndTrackingEnabled,
        deadEndMaxEntries: opts.settings.deadEndMaxEntries,
        stagnationResetEnabled: opts.settings.stagnationResetEnabled,
        stagnationRollbackThreshold: opts.settings.stagnationRollbackThreshold,
        stagnationPlateauWindow: opts.settings.stagnationPlateauWindow,
        stagnationPlateauEpsilon: opts.settings.stagnationPlateauEpsilon,
        stagnationDistillTopLessons: opts.settings.stagnationDistillTopLessons,
        explorationMode: opts.settings.explorationMode,
        explorationCollapseGuard: opts.settings.explorationCollapseGuard,
        explorationCollapseAutoMitigation: opts.settings.explorationCollapseAutoMitigation,
        notifyWebhookUrl: opts.settings.notifyWebhookUrl,
        notifyOn: opts.settings.notifyOn,
        controller: opts.controller,
        events: opts.events,
        hookBus,
        loadedExtensions,
        runtimeSession: opts.providerBundle.runtimeSession,
      }) ??
      new GenerationRunner({
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
        requirePlaybookApproval: opts.requirePlaybookApproval ?? false,
        contextBudgetTokens: opts.settings.contextBudgetTokens,
        curatorEnabled: opts.settings.curatorEnabled,
        curatorConsolidateEveryNGens: opts.settings.curatorConsolidateEveryNGens,
        softHintsEnabled: opts.settings.softHintsEnabled,
        hintStyle: opts.settings.hintStyle,
        skillMaxLessons: opts.settings.skillMaxLessons,
        deadEndTrackingEnabled: opts.settings.deadEndTrackingEnabled,
        deadEndMaxEntries: opts.settings.deadEndMaxEntries,
        stagnationResetEnabled: opts.settings.stagnationResetEnabled,
        stagnationRollbackThreshold: opts.settings.stagnationRollbackThreshold,
        stagnationPlateauWindow: opts.settings.stagnationPlateauWindow,
        stagnationPlateauEpsilon: opts.settings.stagnationPlateauEpsilon,
        stagnationDistillTopLessons: opts.settings.stagnationDistillTopLessons,
        explorationMode: opts.settings.explorationMode,
        explorationCollapseGuard: opts.settings.explorationCollapseGuard,
        explorationCollapseAutoMitigation: opts.settings.explorationCollapseAutoMitigation,
        notifyWebhookUrl: opts.settings.notifyWebhookUrl,
        notifyOn: opts.settings.notifyOn,
        controller: opts.controller,
        events: opts.events,
        hookBus,
        loadedExtensions,
        runtimeSession: opts.providerBundle.runtimeSession,
      });

    await runner.run(asRunId(opts.runId), opts.generations);
  } finally {
    store.close();
    opts.providerBundle.close?.();
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
  settings?: AppSettings;
  controller: LoopController;
  events: EventStreamEmitter;
  deps?: AgentTaskCustomStartRunDeps;
}): Promise<void> {
  const executeTask = opts.deps?.executeAgentTaskSolve ?? executeAgentTaskSolve;
  const { hookBus, loadedExtensions } = opts.settings
    ? await initializeHookBus({
        extensions: opts.settings.extensions,
        failFast: opts.settings.extensionFailFast,
      })
    : { hookBus: null, loadedExtensions: [] };

  emitHook(hookBus, HookEvents.RUN_START, {
    run_id: opts.runId,
    scenario: opts.scenarioName,
    target_generations: opts.generations,
    family: "agent_task",
    saved_custom: true,
    loaded_extensions: loadedExtensions,
  });

  opts.events.emit("run_started", {
    run_id: opts.runId,
    scenario: opts.scenarioName,
    target_generations: opts.generations,
    family: "agent_task",
    saved_custom: true,
  });
  const taskPlan = createRuntimeTaskPlan({
    runId: opts.runId,
    steps: SAVED_AGENT_TASK_PLAN_STEPS,
    events: opts.events,
  });
  publishTaskPlan(taskPlan, (publisher) => publisher.initial({
    activeStepId: "prepare_context",
    summary: "Preparing the saved agent task.",
  }));
  let taskPlanFinished = false;
  const finishTaskPlan = (
    status: "completed" | "failed" | "interrupted",
    summary: string,
  ): void => {
    if (taskPlanFinished) {
      return;
    }
    taskPlanFinished = true;
    publishTaskPlan(taskPlan, (publisher) => publisher.terminal(status, { summary }));
  };
  let activeGeneration: number | null = null;
  let completedGenerations = 0;
  let bestScore: number | undefined;
  try {
    await opts.controller.waitAtBoundary();
    emitHook(hookBus, HookEvents.GENERATION_START, {
      run_id: opts.runId,
      scenario: opts.scenarioName,
      generation: 1,
      family: "agent_task",
      saved_custom: true,
    });
    activeGeneration = 1;
    opts.events.emit("generation_started", { run_id: opts.runId, generation: 1 });

    let result: Awaited<ReturnType<typeof executeAgentTaskSolve>>;
    try {
      result = await executeTask({
        provider: opts.provider,
        created: {
          name: opts.scenarioName,
          spec: opts.entry.spec,
        },
        generations: opts.generations,
        ...(hookBus ? { hookBus } : {}),
        onProgress: (progress) => {
          reportSavedAgentTaskProgress(taskPlan, progress);
        },
      });
    } catch (error) {
      const stopRequest = isRunStopRequestedError(error)
        ? error
        : opts.controller.getStopRequest();
      if (stopRequest) {
        throw stopRequest;
      }
      const message = error instanceof Error ? error.message : String(error);
      emitHook(hookBus, HookEvents.GENERATION_END, {
        run_id: opts.runId,
        scenario: opts.scenarioName,
        generation: 1,
        status: "failed",
        family: "agent_task",
        saved_custom: true,
        error: message,
      });
      activeGeneration = null;
      emitHook(hookBus, HookEvents.RUN_END, {
        run_id: opts.runId,
        scenario: opts.scenarioName,
        status: "failed",
        completed_generations: 0,
        best_score: 0,
        elo: 1000,
        family: "agent_task",
        saved_custom: true,
        error: message,
      });
      throw error;
    }
    bestScore = readBestScore(result.result);
    completedGenerations = normalizeCompletedGenerations(result.progress);

    for (let generation = 1; generation <= completedGenerations; generation++) {
      if (generation > 1) {
        emitHook(hookBus, HookEvents.GENERATION_START, {
          run_id: opts.runId,
          scenario: opts.scenarioName,
          generation,
          family: "agent_task",
          saved_custom: true,
        });
        activeGeneration = generation;
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
      activeGeneration = null;
      emitHook(hookBus, HookEvents.GENERATION_END, {
        run_id: opts.runId,
        scenario: opts.scenarioName,
        generation,
        status: "completed",
        mean_score: bestScore,
        best_score: bestScore,
        elo: 1000,
        gate_decision: "advance",
        family: "agent_task",
        saved_custom: true,
        rounds_completed: completedGenerations,
      });
    }
    if (activeGeneration !== null) {
      const completedGeneration = activeGeneration;
      activeGeneration = null;
      emitHook(hookBus, HookEvents.GENERATION_END, {
        run_id: opts.runId,
        scenario: opts.scenarioName,
        generation: completedGeneration,
        status: "completed",
        mean_score: bestScore,
        best_score: bestScore,
        elo: 1000,
        gate_decision: "advance",
        family: "agent_task",
        saved_custom: true,
        rounds_completed: completedGenerations,
      });
    }
    opts.controller.throwIfStopRequested({
      completedGenerations,
      bestScore,
    });
    const completedPayload = {
      run_id: opts.runId,
      completed_generations: completedGenerations,
      best_score: bestScore,
      elo: 1000,
      session_report_path: null,
      dead_ends_found: 0,
      family: "agent_task",
      saved_custom: true,
    };
    emitHook(hookBus, HookEvents.RUN_END, {
      ...completedPayload,
      scenario: opts.scenarioName,
      status: "completed",
    });
    finishTaskPlan("completed", "Saved agent task completed.");
    opts.events.emit("run_completed", completedPayload);
  } catch (error) {
    const stopRequest = isRunStopRequestedError(error)
      ? error
      : opts.controller.getStopRequest();
    if (!stopRequest) {
      finishTaskPlan("failed", "Saved agent task failed before completion.");
      throw error;
    }
    const stopped = stopRequest.withProgress({
      completedGenerations,
      ...(bestScore === undefined ? {} : { bestScore }),
    });
    if (activeGeneration !== null) {
      emitResolvedTerminalHook(hookBus, HookEvents.GENERATION_END, {
        run_id: opts.runId,
        scenario: opts.scenarioName,
        generation: activeGeneration,
        status: "stopped",
        family: "agent_task",
        saved_custom: true,
      });
    }
    emitResolvedTerminalHook(hookBus, HookEvents.RUN_END, {
      run_id: opts.runId,
      scenario: opts.scenarioName,
      status: "stopped",
      completed_generations: stopped.completedGenerations,
      ...(stopped.bestScore === undefined ? {} : { best_score: stopped.bestScore }),
      elo: 1000,
      family: "agent_task",
      saved_custom: true,
    });
    finishTaskPlan("interrupted", "Saved agent task was interrupted.");
    throw stopped;
  }
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
  const taskPlan = createRuntimeTaskPlan({
    runId: opts.runId,
    steps: GENERATED_CUSTOM_PLAN_STEPS,
    events: opts.events,
  });
  publishTaskPlan(taskPlan, (publisher) => publisher.initial({
    activeStepId: "execute_scenario",
    summary: "Starting the generated scenario run.",
  }));
  let taskPlanFinished = false;
  const finishTaskPlan = (
    status: "completed" | "failed" | "interrupted",
    summary: string,
  ): void => {
    if (taskPlanFinished) {
      return;
    }
    taskPlanFinished = true;
    publishTaskPlan(taskPlan, (publisher) => publisher.terminal(status, { summary }));
  };

  let bestScoreOverall = 0;
  let completedGenerations = 0;
  try {
    for (let generation = 1; generation <= opts.generations; generation++) {
      publishTaskPlan(taskPlan, (publisher) => publisher.progress({
        activeStepId: "execute_scenario",
        stepDetails: {
          execute_scenario: {
            detail: `Running generation ${generation} of ${opts.generations}`,
          },
        },
      }));
      await opts.controller.waitAtBoundary({
        completedGenerations,
        ...(completedGenerations === 0 ? {} : { bestScore: bestScoreOverall }),
      });
      opts.events.emit("generation_started", { run_id: opts.runId, generation });

      const result = await executeScenario({
        customDir,
        name: opts.scenarioName,
        family: opts.family,
        seed: generation,
        ...(typeof maxSteps === "number" ? { maxSteps } : {}),
      });

      bestScoreOverall = Math.max(bestScoreOverall, result.score);
      completedGenerations = generation;
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
      opts.controller.throwIfStopRequested({
        completedGenerations,
        bestScore: bestScoreOverall,
      });
    }

    opts.controller.throwIfStopRequested({
      completedGenerations,
      ...(completedGenerations === 0 ? {} : { bestScore: bestScoreOverall }),
    });
    publishTaskPlan(taskPlan, (publisher) => publisher.progress({
      activeStepId: "aggregate_results",
      completedStepIds: ["execute_scenario"],
      summary: "Scenario generations completed; aggregating results.",
    }));
    publishTaskPlan(taskPlan, (publisher) => publisher.progress({
      activeStepId: "finalize_run",
      completedStepIds: ["execute_scenario", "aggregate_results"],
    }));
    finishTaskPlan("completed", "Generated scenario run completed.");
    opts.events.emit("run_completed", {
      run_id: opts.runId,
      completed_generations: completedGenerations,
      best_score: bestScoreOverall,
      elo: 1000,
      session_report_path: null,
      dead_ends_found: 0,
      family: opts.family,
      generated_custom: true,
    });
  } catch (error) {
    const stopRequest = isRunStopRequestedError(error)
      ? error
      : opts.controller.getStopRequest();
    if (stopRequest) {
      finishTaskPlan("interrupted", "Generated scenario run was interrupted.");
      throw stopRequest.withProgress({
        completedGenerations,
        ...(completedGenerations === 0 ? {} : { bestScore: bestScoreOverall }),
      });
    }
    finishTaskPlan("failed", "Generated scenario run failed before completion.");
    throw error;
  }
}

function emitHook(
  hookBus: HookBus | null,
  name: HookEvents,
  payload: Record<string, unknown>,
): void {
  if (!hookBus?.hasHandlers(name)) {
    return;
  }
  const event = hookBus.emit(name, payload);
  event.raiseIfBlocked();
}

function emitResolvedTerminalHook(
  hookBus: HookBus | null,
  name: HookEvents,
  payload: Record<string, unknown>,
): void {
  try {
    emitHook(hookBus, name, payload);
  } catch {
    // A hook cannot reclassify an already-resolved failed or stopped run.
  }
}
