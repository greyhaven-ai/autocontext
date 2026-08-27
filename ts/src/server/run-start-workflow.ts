import { join } from "node:path";

import type { AppSettings } from "../config/index.js";
import { asDbPath, asRunId } from "../domain/ids.js";
import { isRunStopRequestedError, type LoopController } from "../loop/controller.js";
import type { EventStreamEmitter } from "../loop/events.js";
import { GenerationRunner } from "../loop/generation-runner.js";
import { createAgentTaskPlanPublisher } from "../loop/agent-task-plan.js";
import {
  createAgentProgressNotePublisher,
  type AgentProgressNoteInput,
} from "../loop/agent-progress-note.js";
import type { RoleProviderBundle } from "../providers/index.js";
import { assertFamilyContract } from "../scenarios/family-interfaces.js";
import type { ScenarioInterface } from "../scenarios/game-interface.js";
import type { CustomScenarioEntry } from "../scenarios/custom-loader.js";
import { executeGeneratedScenarioEntry } from "../scenarios/codegen/executor.js";
import {
  TASK_DATA_METADATA_MARKER,
  TASK_DATA_TRUNCATION_WARNING,
} from "../scenarios/improvement-task-contract.js";
import {
  executeAgentTaskSolve,
  type AgentTaskSolveProgress,
} from "../knowledge/agent-task-solve-execution.js";
import { HookEvents, initializeHookBus, type HookBus } from "../extensions/index.js";
import type { ScenarioFamilyName } from "../scenarios/families.js";
import { SCENARIO_REGISTRY } from "../scenarios/registry.js";
import { SQLiteStore } from "../storage/index.js";
import type { LLMProvider, RoundResult } from "../types/index.js";
import {
  AgentTaskOutcomeV1Schema,
  agentTaskOutcomeReceiptV1,
  type AgentTaskOutcomeV1,
} from "../knowledge/agent-task-outcome.js";

const SAVED_AGENT_TASK_PLAN_STEPS = [
  { id: "prepare_context", label: "Prepare task context" },
  { id: "draft_response", label: "Draft the initial response" },
  { id: "improve_response", label: "Evaluate and refine the response" },
  { id: "finalize_result", label: "Package the best result" },
] as const;

const GENERATED_CUSTOM_PLAN_STEPS = [
  { id: "execute_scenario", label: "Execute scenario generations" },
  { id: "aggregate_results", label: "Aggregate generation results" },
  { id: "finalize_run", label: "Finalize the run" },
] as const;

type RuntimeTaskPlanPublisher = NonNullable<ReturnType<typeof createAgentTaskPlanPublisher>>;
type RuntimeProgressNotePublisher = NonNullable<
  ReturnType<typeof createAgentProgressNotePublisher>
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

function createRuntimeProgressNotes(opts: {
  runId: string;
  events: EventStreamEmitter;
}): RuntimeProgressNotePublisher | null {
  try {
    return createAgentProgressNotePublisher(opts);
  } catch {
    return null;
  }
}

function publishProgressNote(
  progressNotes: RuntimeProgressNotePublisher | null,
  input: AgentProgressNoteInput,
): void {
  try {
    progressNotes?.publish(input);
  } catch {
    // Progress-note telemetry must never alter run results.
  }
}

function reportSavedAgentTaskProgress(
  taskPlan: RuntimeTaskPlanPublisher | null,
  progressNotes: RuntimeProgressNotePublisher | null,
  progress: AgentTaskSolveProgress,
): void {
  const evaluationDetail =
    progress.round === undefined
      ? "Evaluating the current response"
      : `Evaluating the current response in round ${progress.round}`;
  const revisionDetail =
    progress.round === undefined
      ? "Revising the response after evaluation"
      : `Revising the response after evaluation round ${progress.round}`;
  const generation = progress.round ?? 0;
  if (progress.phase === "context_preparation") {
    if (progress.status === "started") {
      publishTaskPlan(taskPlan, (publisher) =>
        publisher.progress({ activeStepId: "prepare_context" }),
      );
      publishProgressNote(progressNotes, {
        generation: 0,
        kind: "discovery",
        text: "Preparing the task context for drafting.",
      });
    } else {
      publishTaskPlan(taskPlan, (publisher) =>
        publisher.progress({
          activeStepId: "draft_response",
          completedStepIds: ["prepare_context"],
        }),
      );
      publishProgressNote(progressNotes, {
        generation: 0,
        kind: "discovery",
        text: "The task context is prepared for drafting.",
      });
    }
    return;
  }
  if (progress.phase === "draft") {
    if (progress.status === "started") {
      publishTaskPlan(taskPlan, (publisher) =>
        publisher.progress({
          activeStepId: "draft_response",
          completedStepIds: ["prepare_context"],
        }),
      );
      publishProgressNote(progressNotes, {
        generation: 0,
        kind: "discovery",
        text: "Drafting the initial response.",
      });
    } else {
      publishTaskPlan(taskPlan, (publisher) =>
        publisher.progress({
          activeStepId: "improve_response",
          completedStepIds: ["prepare_context", "draft_response"],
          stepDetails: { improve_response: { detail: evaluationDetail } },
        }),
      );
      publishProgressNote(progressNotes, {
        generation: 0,
        kind: "discovery",
        text: "The initial response is ready for evaluation.",
      });
    }
    return;
  }
  if (progress.phase === "evaluation") {
    publishTaskPlan(taskPlan, (publisher) =>
      publisher.progress({
        activeStepId: "improve_response",
        completedStepIds: ["prepare_context", "draft_response"],
        stepDetails: { improve_response: { detail: evaluationDetail } },
      }),
    );
    const scoreDetail = progress.roundResult
      ? ` with a score of ${progress.roundResult.score.toFixed(3)}`
      : "";
    publishProgressNote(progressNotes, {
      generation,
      kind: progress.status === "started" ? "verification" : "discovery",
      text:
        progress.status === "started"
          ? `${evaluationDetail}.`
          : progress.round === undefined
            ? `Evaluation scored the current response${scoreDetail}.`
            : `Evaluation round ${progress.round} scored the current response${scoreDetail}.`,
    });
    return;
  }
  if (progress.phase === "revision") {
    if (progress.status === "started") {
      publishTaskPlan(taskPlan, (publisher) =>
        publisher.replan({
          activeStepId: "improve_response",
          completedStepIds: ["prepare_context", "draft_response"],
          summary: `${revisionDetail}.`,
          stepDetails: { improve_response: { detail: revisionDetail } },
        }),
      );
    } else {
      publishTaskPlan(taskPlan, (publisher) =>
        publisher.progress({
          activeStepId: "improve_response",
          completedStepIds: ["prepare_context", "draft_response"],
          stepDetails: { improve_response: { detail: revisionDetail } },
        }),
      );
    }
    publishProgressNote(progressNotes, {
      generation,
      kind: progress.status === "started" ? "decision" : "discovery",
      text:
        progress.status === "started"
          ? `${revisionDetail}.`
          : progress.round === undefined
            ? "The revised response is ready for evaluation."
            : `The response revised after round ${progress.round} is ready for evaluation.`,
    });
    return;
  }
  if (progress.phase === "finalization") {
    publishTaskPlan(taskPlan, (publisher) =>
      publisher.progress({
        activeStepId: "finalize_result",
        completedStepIds: ["prepare_context", "draft_response", "improve_response"],
      }),
    );
    publishProgressNote(progressNotes, {
      generation,
      kind: "decision",
      text:
        progress.status === "started"
          ? "Packaging the best scored response for retention."
          : "The best scored response is packaged for retention.",
    });
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
  run(runId: string, generations: number, minimumGenerations?: number): Promise<unknown>;
}

export interface BuiltInGameStartRunDeps {
  resolveScenarioClass?: (scenarioName: string) => ScenarioClass | undefined;
  createStore?: (dbPath: string) => StartRunStoreLike;
  createRunner?: (opts: ConstructorParameters<typeof GenerationRunner>[0]) => StartRunRunnerLike;
}

export async function executeBuiltInGameStartRun(opts: {
  runId: string;
  scenarioName: string;
  minimumGenerations?: number;
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
        agentProvider: opts.providerBundle.defaultConfig.providerType,
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
        agentProvider: opts.providerBundle.defaultConfig.providerType,
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

    await runner.run(asRunId(opts.runId), opts.generations, opts.minimumGenerations ?? 1);
  } finally {
    store.close();
    opts.providerBundle.close?.();
  }
}

export interface AgentTaskCustomStartRunDeps {
  executeAgentTaskSolve?: typeof executeAgentTaskSolve;
  createStore?: (dbPath: string) => SavedAgentTaskRunStore;
  now?: () => number;
}

type SavedAgentTaskRunStore = Pick<
  SQLiteStore,
  | "migrate"
  | "createRun"
  | "updateRunStatus"
  | "saveAgentTaskOutcome"
  | "upsertGeneration"
  | "appendAgentOutput"
  | "close"
>;

function readBestScore(result: Record<string, unknown>, fallback = 0): number {
  const raw = result.best_score;
  return typeof raw === "number" && Number.isFinite(raw) ? raw : fallback;
}

function normalizeCompletedGenerations(progress: number): number {
  return Number.isFinite(progress) ? Math.max(0, Math.floor(progress)) : 0;
}

function readAgentTaskOutcome(
  result: Awaited<ReturnType<typeof executeAgentTaskSolve>>,
): AgentTaskOutcomeV1 | null {
  // Some downstream embedders inject a legacy solve implementation in tests or
  // during a rolling upgrade. A present outcome is strict; an absent outcome
  // preserves that compatibility boundary without fabricating contract fields.
  const rawOutcome: unknown = result.outcome;
  return rawOutcome === undefined ? null : AgentTaskOutcomeV1Schema.parse(rawOutcome);
}

function hasTruncatedStructuredTaskData(spec: Record<string, unknown>): boolean {
  const fields = [
    spec.sampleInput,
    spec.sample_input,
    spec.referenceContext,
    spec.reference_context,
    spec.evaluationContext,
    spec.evaluation_context,
  ];
  return fields.some(
    (field) =>
      typeof field === "string" &&
      field.includes(`${TASK_DATA_METADATA_MARKER}:`) &&
      field.includes(TASK_DATA_TRUNCATION_WARNING),
  );
}

export async function executeAgentTaskCustomStartRun(opts: {
  runId: string;
  scenarioName: string;
  entry: CustomScenarioEntry;
  minimumGenerations?: number;
  generations: number;
  provider: LLMProvider;
  settings?: AppSettings;
  persistence?: {
    dbPath: string;
    migrationsDir: string;
    agentProvider?: string;
  };
  controller: LoopController;
  events: EventStreamEmitter;
  deps?: AgentTaskCustomStartRunDeps;
}): Promise<void> {
  const executeTask = opts.deps?.executeAgentTaskSolve ?? executeAgentTaskSolve;
  const minimumGenerations =
    opts.minimumGenerations ?? readSpecMinimumGenerations(opts.entry.spec);
  if (
    !Number.isInteger(minimumGenerations) ||
    minimumGenerations < 1 ||
    minimumGenerations > opts.generations
  ) {
    throw new Error("minimum_generations must be between 1 and generations");
  }
  const now = opts.deps?.now ?? Date.now;
  const { hookBus, loadedExtensions } = opts.settings
    ? await initializeHookBus({
        extensions: opts.settings.extensions,
        failFast: opts.settings.extensionFailFast,
      })
    : { hookBus: null, loadedExtensions: [] };

  emitHook(hookBus, HookEvents.RUN_START, {
    run_id: opts.runId,
    scenario: opts.scenarioName,
    minimum_generations: minimumGenerations,
    target_generations: opts.generations,
    family: "agent_task",
    saved_custom: true,
    loaded_extensions: loadedExtensions,
  });

  opts.events.emit("run_started", {
    run_id: opts.runId,
    scenario: opts.scenarioName,
    minimum_generations: minimumGenerations,
    target_generations: opts.generations,
    family: "agent_task",
    saved_custom: true,
  });
  if (hasTruncatedStructuredTaskData(opts.entry.spec)) {
    opts.events.emit(
      "monitor_alert",
      {
        alert_id: `${opts.runId}:truncated-task-data`,
        condition_id: "structured_task_data_truncated",
        condition_name: "Mission data was truncated",
        condition_type: "data_integrity",
        scope: `run:${opts.runId}`,
        detail:
          "At least one mission data source was truncated before execution. Treat conclusions as limited to the retained content.",
      },
      "monitor",
    );
  }
  const taskPlan = createRuntimeTaskPlan({
    runId: opts.runId,
    steps: SAVED_AGENT_TASK_PLAN_STEPS,
    events: opts.events,
  });
  publishTaskPlan(taskPlan, (publisher) =>
    publisher.initial({
      activeStepId: "prepare_context",
      summary: "Preparing the saved agent task.",
    }),
  );
  const progressNotes = createRuntimeProgressNotes({
    runId: opts.runId,
    events: opts.events,
  });
  publishProgressNote(progressNotes, {
    generation: 0,
    kind: "intent",
    text: "Prepare the task context, improve the response through scored rounds, and retain the best result.",
  });
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
  const startedGenerations = new Set<number>();
  const generationStartedAtMs = new Map<number, number>();
  // Candidate work happens before the corresponding evaluation lifecycle is
  // emitted. Retain that earlier boundary so durable round timing includes
  // initial drafting and revisions without publishing a generation that may
  // never be evaluated (for example, an unchanged revision).
  const pendingGenerationStartedAtMs = new Map<number, number>();
  const completedGenerationNumbers = new Set<number>();
  const persistedOutputGenerations = new Set<number>();
  let store: SavedAgentTaskRunStore | null = null;
  let storedRun = false;

  const updateStoredRunStatusBestEffort = (status: string): void => {
    if (!store || !storedRun) return;
    try {
      store.updateRunStatus(opts.runId, status);
    } catch {
      // Do not replace the already-resolved run outcome with a secondary
      // persistence failure while recording that outcome.
    }
  };

  const startGeneration = (generation: number): void => {
    if (
      !Number.isInteger(generation) ||
      generation < 1 ||
      startedGenerations.has(generation) ||
      completedGenerationNumbers.has(generation)
    ) {
      return;
    }
    emitHook(hookBus, HookEvents.GENERATION_START, {
      run_id: opts.runId,
      scenario: opts.scenarioName,
      generation,
      family: "agent_task",
      saved_custom: true,
    });
    startedGenerations.add(generation);
    generationStartedAtMs.set(
      generation,
      pendingGenerationStartedAtMs.get(generation) ?? now(),
    );
    pendingGenerationStartedAtMs.delete(generation);
    activeGeneration = generation;
    opts.events.emit("generation_started", { run_id: opts.runId, generation });
  };

  const markPendingGenerationWork = (generation: number): void => {
    if (
      !Number.isInteger(generation) ||
      generation < 1 ||
      generation > opts.generations ||
      startedGenerations.has(generation) ||
      completedGenerationNumbers.has(generation) ||
      pendingGenerationStartedAtMs.has(generation)
    ) {
      return;
    }
    pendingGenerationStartedAtMs.set(generation, now());
  };

  const completeGeneration = (input: {
    generation: number;
    roundResult?: RoundResult;
    runningBestScore?: number;
    fallbackScore?: number;
  }): void => {
    const generation = input.generation;
    if (
      !Number.isInteger(generation) ||
      generation < 1 ||
      completedGenerationNumbers.has(generation)
    ) {
      return;
    }
    startGeneration(generation);

    const roundScore = input.roundResult?.score ?? input.fallbackScore ?? 0;
    const runningBestScore =
      input.runningBestScore !== undefined && Number.isFinite(input.runningBestScore)
        ? input.runningBestScore
        : Math.max(bestScore ?? 0, roundScore);
    bestScore = runningBestScore;
    completedGenerations = Math.max(completedGenerations, generation);
    completedGenerationNumbers.add(generation);
    const reportedDurationMs =
      typeof input.roundResult?.roundDurationMs === "number" &&
      Number.isFinite(input.roundResult.roundDurationMs)
        ? Math.max(0, input.roundResult.roundDurationMs)
        : 0;
    const startedAtMs = generationStartedAtMs.get(generation);
    const observedDurationMs = startedAtMs === undefined ? 0 : Math.max(0, now() - startedAtMs);
    const durationMs = Math.max(reportedDurationMs, observedDurationMs);
    generationStartedAtMs.delete(generation);

    if (store) {
      const dimensionScores = input.roundResult?.dimensionScores;
      store.upsertGeneration(opts.runId, generation, {
        meanScore: roundScore,
        bestScore: runningBestScore,
        elo: 1000,
        wins: 0,
        losses: 0,
        gateDecision: "advance",
        status: "completed",
        durationSeconds: durationMs / 1000,
        dimensionSummaryJson: dimensionScores ? JSON.stringify(dimensionScores) : null,
        scoringBackend: "agent_task",
        evaluatorEpoch: input.roundResult?.evaluatorEpoch ?? null,
      });
      const output = input.roundResult?.output;
      if (typeof output === "string" && output.length > 0) {
        store.appendAgentOutput(opts.runId, generation, "competitor", output);
        persistedOutputGenerations.add(generation);
      }
      const reasoning = input.roundResult?.reasoning;
      if (typeof reasoning === "string" && reasoning.length > 0) {
        store.appendAgentOutput(opts.runId, generation, "analyst", reasoning);
      }
    }

    const evaluationPayload = input.roundResult
      ? {
          reasoning: input.roundResult.reasoning,
          dimension_scores: { ...input.roundResult.dimensionScores },
          judge_failed: input.roundResult.judgeFailed,
          ...(durationMs === 0 ? {} : { round_duration_ms: durationMs }),
          evaluator_epoch: input.roundResult.evaluatorEpoch ?? null,
        }
      : {};
    const elapsedSeconds = durationMs / 1000;
    opts.events.emit("generation_timing", {
      run_id: opts.runId,
      generation,
      elapsed_seconds: elapsedSeconds,
    });
    opts.events.emit("generation_completed", {
      run_id: opts.runId,
      generation,
      mean_score: roundScore,
      best_score: runningBestScore,
      elo: 1000,
      gate_decision: "advance",
      family: "agent_task",
      rounds_completed: generation,
      ...evaluationPayload,
    });
    if (activeGeneration === generation) {
      activeGeneration = null;
    }
    emitHook(hookBus, HookEvents.GENERATION_END, {
      run_id: opts.runId,
      scenario: opts.scenarioName,
      generation,
      status: "completed",
      mean_score: roundScore,
      best_score: runningBestScore,
      elo: 1000,
      gate_decision: "advance",
      family: "agent_task",
      saved_custom: true,
      rounds_completed: generation,
      ...evaluationPayload,
    });
  };

  try {
    await opts.controller.waitAtBoundary();

    let result: Awaited<ReturnType<typeof executeAgentTaskSolve>>;
    let progressLifecycleError: unknown;
    try {
      if (opts.persistence) {
        store =
          opts.deps?.createStore?.(opts.persistence.dbPath) ??
          new SQLiteStore(asDbPath(opts.persistence.dbPath));
        store.migrate(opts.persistence.migrationsDir);
        store.createRun(
          opts.runId,
          opts.scenarioName,
          opts.generations,
          "agent_task",
          opts.persistence.agentProvider ?? opts.provider.name ?? "",
          minimumGenerations,
        );
        storedRun = true;
      }
      result = await executeTask({
        provider: opts.provider,
        created: {
          name: opts.scenarioName,
          spec: opts.entry.spec,
        },
        minimumGenerations,
        generations: opts.generations,
        ...(hookBus ? { hookBus } : {}),
        onProgress: (progress) => {
          reportSavedAgentTaskProgress(taskPlan, progressNotes, progress);
          try {
            if (progress.status === "started" && progress.phase === "draft") {
              markPendingGenerationWork(1);
            }
            if (
              progress.status === "started" &&
              progress.phase === "revision" &&
              progress.round !== undefined
            ) {
              markPendingGenerationWork(progress.round + 1);
            }
            if (progress.phase === "evaluation" && progress.round !== undefined) {
              if (progress.status === "started") {
                startGeneration(progress.round);
              } else if (progress.roundResult) {
                completeGeneration({
                  generation: progress.round,
                  roundResult: progress.roundResult,
                  runningBestScore: progress.bestScore,
                });
              }
            }
          } catch (error) {
            // The solve progress bridge is deliberately fire-and-forget. Retain
            // lifecycle hook failures here so they can still fail the run after
            // the solver returns instead of being swallowed as telemetry noise.
            progressLifecycleError ??= error;
          }
        },
      });
      if (progressLifecycleError !== undefined) {
        throw progressLifecycleError;
      }
    } catch (error) {
      const stopRequest = isRunStopRequestedError(error) ? error : opts.controller.getStopRequest();
      if (stopRequest) {
        throw stopRequest;
      }
      const message = error instanceof Error ? error.message : String(error);
      updateStoredRunStatusBestEffort("failed");
      if (activeGeneration !== null) {
        emitHook(hookBus, HookEvents.GENERATION_END, {
          run_id: opts.runId,
          scenario: opts.scenarioName,
          generation: activeGeneration,
          status: "failed",
          family: "agent_task",
          saved_custom: true,
          error: message,
        });
        activeGeneration = null;
      }
      emitHook(hookBus, HookEvents.RUN_END, {
        run_id: opts.runId,
        scenario: opts.scenarioName,
        status: "failed",
        completed_generations: completedGenerations,
        best_score: bestScore ?? 0,
        elo: 1000,
        family: "agent_task",
        saved_custom: true,
        error: message,
      });
      throw error;
    }
    const agentTaskOutcome = readAgentTaskOutcome(result);
    const finalBestScore = agentTaskOutcome?.best_score
      ?? readBestScore(result.result, bestScore ?? 0);
    const finalCompletedGenerations = agentTaskOutcome?.completed_iterations
      ?? normalizeCompletedGenerations(result.progress);

    // Legacy/injected solvers may only return a final progress count and best
    // score. Fill any lifecycle gaps after completion without duplicating the
    // rounds already delivered live by the structured progress bridge.
    for (let generation = 1; generation <= finalCompletedGenerations; generation++) {
      completeGeneration({ generation, fallbackScore: finalBestScore });
    }
    completedGenerations = Math.max(completedGenerations, finalCompletedGenerations);
    bestScore = finalBestScore;
    const example = Array.isArray(result.result.example_outputs)
      ? result.result.example_outputs[0]
      : null;
    const retainedOutput =
      example && typeof example === "object" && typeof example.output === "string"
        ? example.output
        : "";
    const retainedReasoning =
      example && typeof example === "object" && typeof example.reasoning === "string"
        ? example.reasoning
        : "";
    const bestStrategy = result.result.best_strategy;
    const rawBestRound =
      bestStrategy &&
      typeof bestStrategy === "object" &&
      !Array.isArray(bestStrategy) &&
      typeof bestStrategy.best_round === "number"
        ? bestStrategy.best_round
        : completedGenerations;
    const retainedBestRound = agentTaskOutcome?.best_iteration ?? Math.max(
      1,
      Math.min(completedGenerations || 1, Math.floor(rawBestRound)),
    );
    if (store && !persistedOutputGenerations.has(retainedBestRound) && retainedOutput) {
      store.appendAgentOutput(opts.runId, retainedBestRound, "competitor", retainedOutput);
      if (retainedReasoning) {
        store.appendAgentOutput(opts.runId, retainedBestRound, "analyst", retainedReasoning);
      }
    }
    if (store && typeof result.result.playbook === "string" && result.result.playbook) {
      store.appendAgentOutput(opts.runId, retainedBestRound, "coach", result.result.playbook);
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
    if (store && storedRun && agentTaskOutcome) {
      store.saveAgentTaskOutcome(opts.runId, JSON.stringify(agentTaskOutcome));
    }
    const completedPayload = {
      run_id: opts.runId,
      completed_generations: completedGenerations,
      best_score: bestScore,
      elo: 1000,
      session_report_path: null,
      dead_ends_found: 0,
      family: "agent_task",
      saved_custom: true,
      ...(agentTaskOutcome
        ? { agent_task_outcome: agentTaskOutcomeReceiptV1(agentTaskOutcome) }
        : {}),
    };
    emitHook(hookBus, HookEvents.RUN_END, {
      ...completedPayload,
      scenario: opts.scenarioName,
      status: "completed",
    });
    const finalActionId = "agent-final-result";
    const finalArtifactId = "agent-final-output";
    const retainedPreview = retainedOutput.slice(0, 2_000);
    if (retainedOutput) {
      opts.events.emit("action_detail", {
        run_id: opts.runId,
        action_id: finalActionId,
        name: "Final result",
        kind: "agent",
        status: "completed",
        role: "competitor",
        generation: retainedBestRound,
        activity_kind: "completion",
        output: {
          score: bestScore,
          _autowork: {
            version: 1,
            changes: [],
            artifacts: [
              {
                id: finalArtifactId,
                previewKind: "markdown",
                preview: retainedPreview,
                previewTruncated: retainedOutput.length > retainedPreview.length,
              },
            ],
            checks: [],
          },
        },
        artifacts: [
          {
            id: finalArtifactId,
            name: "Final result.md",
            media_type: "text/markdown",
          },
        ],
      });
    }
    finishTaskPlan("completed", "Saved agent task completed.");
    publishProgressNote(progressNotes, {
      generation: completedGenerations,
      kind: "decision",
      text:
        bestScore === undefined
          ? `Retained the best result from ${completedGenerations} completed evaluation rounds.`
          : `Retained the best result from ${completedGenerations} completed evaluation rounds with a best score of ${bestScore.toFixed(3)}.`,
      ...(retainedOutput
        ? {
            evidenceTargets: [
              {
                kind: "artifact" as const,
                action_id: finalActionId,
                artifact_id: finalArtifactId,
              },
            ],
          }
        : {}),
    });
    if (store && storedRun) {
      store.updateRunStatus(opts.runId, "completed");
    }
    opts.events.emit("run_completed", completedPayload);
  } catch (error) {
    const stopRequest = isRunStopRequestedError(error) ? error : opts.controller.getStopRequest();
    if (!stopRequest) {
      updateStoredRunStatusBestEffort("failed");
      finishTaskPlan("failed", "Saved agent task failed before completion.");
      publishProgressNote(progressNotes, {
        generation: completedGenerations,
        kind: "blocker",
        text: "The saved agent task could not complete; retained progress remains available for review.",
      });
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
    updateStoredRunStatusBestEffort("stopped");
    finishTaskPlan("interrupted", "Saved agent task was interrupted.");
    throw stopped;
  } finally {
    store?.close();
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

function readSpecMinimumGenerations(spec: Record<string, unknown>): number {
  const raw = spec.minRounds ?? spec.min_rounds;
  if (typeof raw === "number" && Number.isFinite(raw)) {
    return raw;
  }
  if (typeof raw === "string" && raw.trim()) {
    const parsed = Number(raw);
    if (!Number.isNaN(parsed)) {
      return parsed;
    }
  }
  return 1;
}

export async function executeGeneratedCustomStartRun(opts: {
  runId: string;
  scenarioName: string;
  entry: CustomScenarioEntry;
  family: ScenarioFamilyName;
  minimumGenerations?: number;
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
    minimum_generations: opts.minimumGenerations ?? 1,
    target_generations: opts.generations,
    family: opts.family,
    generated_custom: true,
  });
  const taskPlan = createRuntimeTaskPlan({
    runId: opts.runId,
    steps: GENERATED_CUSTOM_PLAN_STEPS,
    events: opts.events,
  });
  publishTaskPlan(taskPlan, (publisher) =>
    publisher.initial({
      activeStepId: "execute_scenario",
      summary: "Starting the generated scenario run.",
    }),
  );
  const progressNotes = createRuntimeProgressNotes({
    runId: opts.runId,
    events: opts.events,
  });
  publishProgressNote(progressNotes, {
    generation: 0,
    kind: "intent",
    text: "Execute the scenario generations, compare their scores, and verify the best result.",
  });
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
      publishTaskPlan(taskPlan, (publisher) =>
        publisher.progress({
          activeStepId: "execute_scenario",
          stepDetails: {
            execute_scenario: {
              detail: `Running generation ${generation} of ${opts.generations}`,
            },
          },
        }),
      );
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
      publishProgressNote(progressNotes, {
        generation,
        kind: "discovery",
        text: `Generation ${generation} completed with a score of ${result.score.toFixed(3)}.`,
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
    publishTaskPlan(taskPlan, (publisher) =>
      publisher.progress({
        activeStepId: "aggregate_results",
        completedStepIds: ["execute_scenario"],
        summary: "Scenario generations completed; aggregating results.",
      }),
    );
    publishTaskPlan(taskPlan, (publisher) =>
      publisher.progress({
        activeStepId: "finalize_run",
        completedStepIds: ["execute_scenario", "aggregate_results"],
      }),
    );
    finishTaskPlan("completed", "Generated scenario run completed.");
    publishProgressNote(progressNotes, {
      generation: completedGenerations,
      kind: "verification",
      text: `Verified ${completedGenerations} completed generations with a best score of ${bestScoreOverall.toFixed(3)}.`,
    });
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
    const stopRequest = isRunStopRequestedError(error) ? error : opts.controller.getStopRequest();
    if (stopRequest) {
      finishTaskPlan("interrupted", "Generated scenario run was interrupted.");
      throw stopRequest.withProgress({
        completedGenerations,
        ...(completedGenerations === 0 ? {} : { bestScore: bestScoreOverall }),
      });
    }
    finishTaskPlan("failed", "Generated scenario run failed before completion.");
    publishProgressNote(progressNotes, {
      generation: completedGenerations,
      kind: "blocker",
      text: "The generated scenario could not complete; retained progress remains available for review.",
    });
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
