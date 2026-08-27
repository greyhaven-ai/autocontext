/**
 * Generation runner — core loop (AC-346 Task 21).
 * Mirrors Python's loop/generation_runner.py (simplified).
 *
 * Loop: for each generation:
 *   1. Build prompts from scenario + knowledge
 *   2. Orchestrate agents (competitor → analyst/coach/architect)
 *   3. Extract strategy → run tournament
 *   4. Backpressure gate (advance/retry/rollback)
 *   5. Persist to SQLite + artifacts
 */

import { Buffer } from "node:buffer";
import { join } from "node:path";

import type {
  CompletionResult,
  LLMProvider,
  ValidatedImageAttachment,
} from "../types/index.js";
import { assertProviderSupportsImageAttachments } from "../providers/image-capability.js";
import { asScenarioName, type RunId, type ScenarioName } from "../domain/ids.js";
import type { ScenarioInterface } from "../scenarios/game-interface.js";
import type { SQLiteStore } from "../storage/index.js";
import { TournamentRunner } from "../execution/tournament.js";
import { BackpressureGate } from "./backpressure.js";
import { applyAnnealingToGateDecision, deterministicAnnealingRandomValue } from "./annealing.js";
import { renderLevyScoutGuidance } from "./levy-scout.js";
import { ArtifactStore, EMPTY_PLAYBOOK_SENTINEL } from "../knowledge/artifact-store.js";
import { PlaybookGuard, PLAYBOOK_MARKERS, missingPlaybookMarkers } from "../knowledge/playbook.js";
import { effectiveHintStyle } from "../knowledge/soft-hints.js";
import { ScoreTrajectoryBuilder } from "../knowledge/trajectory.js";
import {
  compactPromptComponents,
  compactionEntriesForComponents,
} from "../knowledge/semantic-compaction.js";
import { completeWithProviderHooks, HookEvents, HookBus } from "../extensions/index.js";
import { ContextBudget } from "../prompts/context-budget.js";
import {
  parseCuratorLessonResult,
  parseCuratorPlaybookDecision,
} from "../agents/curator-parser.js";
import {
  CompositeNotifier,
  HTTPNotifier,
  StdoutNotifier,
  type EventType,
  type Notifier,
} from "../notifications/index.js";
import {
  isRunStopRequestedError,
  type LoopController,
  type RunStopRequestedError,
} from "./controller.js";
import type { EventStreamEmitter } from "./events.js";
import { createAgentTaskPlanPublisher, type AgentTaskPlanPublisher } from "./agent-task-plan.js";
import {
  createAgentProgressNotePublisher,
  type AgentProgressNoteInput,
  type AgentProgressNotePublisher,
} from "./agent-progress-note.js";
import { StagnationDetector } from "./stagnation.js";
import {
  buildCompetitorPrompt,
  buildCuratorConsolidationPrompt,
  buildCuratorPrompt,
  buildSupportPrompt,
} from "./generation-prompts.js";
import { COMPETITOR_REPAIR_MAX_OUTPUT_TOKENS } from "./generation-execution-step.js";
import {
  createGenerationAttemptWorkflow,
  runGenerationAttemptWorkflow,
} from "./generation-attempt-workflow.js";
import { SolveGenerationBudget } from "../knowledge/solve-generation-budget.js";
import {
  completeGenerationLifecycleWorkflow,
  createGenerationLifecycleWorkflow,
  runGenerationLifecycleWorkflow,
} from "./generation-lifecycle-workflow.js";
import { buildRoleCompletedPayload } from "./generation-side-effect-coordinator.js";
import { GenerationJournal } from "./generation-journal.js";
import {
  completeGenerationLoopRun,
  createGenerationLoopOrchestration,
  failGenerationLoopRun,
  type GenerationLoopOrchestration,
} from "./generation-loop-orchestrator.js";
import { GenerationRecovery } from "./generation-recovery.js";
import {
  PLAYBOOK_UPDATE_SKIPPED_EVENT,
  type PlaybookUpdateSkippedPayload,
} from "./playbook-update-events.js";
import { hasRemainingGenerationCycles } from "./generation-cycle-state.js";
import type { GenerationAttempt } from "./generation-phase-state.js";
import type { GenerationAttemptOrchestration } from "./generation-attempt-orchestrator.js";
import type { GenerationLoopEventSequenceItem } from "./generation-side-effect-coordinator.js";
import {
  consumeFreshStartHint,
  queueFreshStartHint,
  type GenerationRunState,
} from "./generation-run-state.js";
import type { GenerationRole } from "../providers/index.js";
import type { RuntimeSession } from "../session/runtime-session.js";
import {
  detectExplorationCollapse,
  type ExplorationSnapshot,
  type GuidanceChange,
} from "../analytics/exploration-collapse-guard.js";

const BUILT_IN_GAME_PLAN_STEPS = [
  { id: "prepare_run", label: "Prepare the strategy context" },
  { id: "iterate_strategies", label: "Generate, evaluate, and refine strategies" },
  { id: "finalize_run", label: "Finalize run artifacts" },
] as const;

export interface GenerationRunnerOpts {
  provider: LLMProvider;
  agentProvider?: string;
  roleProviders?: Partial<Record<GenerationRole, LLMProvider>>;
  roleModels?: Partial<Record<GenerationRole, string>>;
  scenario: ScenarioInterface;
  store: SQLiteStore;
  runsRoot: string;
  knowledgeRoot: string;
  matchesPerGeneration?: number;
  maxRetries?: number;
  minDelta?: number;
  seedBase?: number;
  playbookMaxVersions?: number;
  requirePlaybookApproval?: boolean;
  contextBudgetTokens?: number;
  curatorEnabled?: boolean;
  curatorConsolidateEveryNGens?: number;
  softHintsEnabled?: boolean;
  hintStyle?: string;
  skillMaxLessons?: number;
  deadEndTrackingEnabled?: boolean;
  deadEndMaxEntries?: number;
  stagnationResetEnabled?: boolean;
  stagnationRollbackThreshold?: number;
  stagnationPlateauWindow?: number;
  stagnationPlateauEpsilon?: number;
  stagnationDistillTopLessons?: number;
  explorationMode?: string;
  experimentalAnnealingEnabled?: boolean;
  experimentalLevyScoutEnabled?: boolean;
  levyScoutAlpha?: number;
  levyScoutScale?: number;
  annealingStartTemperature?: number;
  annealingEndTemperature?: number;
  annealingGenerations?: number;
  explorationCollapseGuard?: boolean;
  explorationCollapseAutoMitigation?: boolean;
  notifyWebhookUrl?: string | null;
  notifyOn?: string;
  notifier?: Notifier | null;
  controller?: LoopController;
  events?: EventStreamEmitter;
  generationTimeBudgetSeconds?: number | null;
  hookBus?: HookBus | null;
  loadedExtensions?: string[];
  runtimeSession?: RuntimeSession;
}

export interface RunResult {
  runId: RunId;
  generationsCompleted: number;
  bestScore: number;
  currentElo: number;
}

export class GenerationRunner {
  #provider: LLMProvider;
  #agentProvider: string;
  #roleProviders: Partial<Record<GenerationRole, LLMProvider>>;
  #roleModels: Partial<Record<GenerationRole, string>>;
  #scenario: ScenarioInterface;
  #scenarioName: ScenarioName;
  #store: SQLiteStore;
  #artifactStore: ArtifactStore;
  #journal: GenerationJournal;
  #recovery: GenerationRecovery;
  #matchesPerGeneration: number;
  #maxRetries: number;
  #gate: BackpressureGate;
  #seedBase: number;
  #playbookGuard: PlaybookGuard;
  #requirePlaybookApproval: boolean;
  #contextBudget: ContextBudget;
  #curatorEnabled: boolean;
  #curatorConsolidateEveryNGens: number;
  #hintStyle: string;
  #skillMaxLessons: number;
  #deadEndTrackingEnabled: boolean;
  #deadEndMaxEntries: number;
  #stagnationResetEnabled: boolean;
  #stagnationDistillTopLessons: number;
  #stagnationDetector: StagnationDetector;
  #explorationMode: string;
  #experimentalAnnealingEnabled: boolean;
  #experimentalLevyScoutEnabled: boolean;
  #levyScoutAlpha: number;
  #levyScoutScale: number;
  #annealingStartTemperature: number;
  #annealingEndTemperature: number;
  #annealingGenerations: number;
  #explorationCollapseGuard: boolean;
  #explorationCollapseAutoMitigation: boolean;
  #notifier: Notifier | null;
  #notifyOn: Set<EventType>;
  #controller: LoopController | null;
  #events: EventStreamEmitter | null;
  #generationTimeBudgetSeconds: number | null;
  #hookBus: HookBus;
  #loadedExtensions: string[];
  #runtimeSession?: RuntimeSession;
  #runState: GenerationRunState | null = null;
  #taskPlan: AgentTaskPlanPublisher | null = null;
  #taskPlanFinished = false;
  #progressNotes: AgentProgressNotePublisher | null = null;

  constructor(opts: GenerationRunnerOpts) {
    this.#provider = opts.provider;
    this.#agentProvider = normalizeAgentProviderName(opts.agentProvider ?? opts.provider.name);
    this.#roleProviders = opts.roleProviders ?? {};
    this.#roleModels = opts.roleModels ?? {};
    this.#scenario = opts.scenario;
    this.#scenarioName = asScenarioName(this.#scenario.name);
    this.#store = opts.store;
    this.#artifactStore = new ArtifactStore({
      runsRoot: opts.runsRoot,
      knowledgeRoot: opts.knowledgeRoot,
      maxPlaybookVersions: opts.playbookMaxVersions,
      hookBus: opts.hookBus ?? null,
    });
    this.#journal = new GenerationJournal({
      store: this.#store,
      artifacts: this.#artifactStore,
      scenario: this.#scenario,
    });
    this.#matchesPerGeneration = opts.matchesPerGeneration ?? 3;
    this.#maxRetries = opts.maxRetries ?? 2;
    this.#gate = new BackpressureGate(opts.minDelta ?? 0.005);
    this.#seedBase = opts.seedBase ?? 1000;
    this.#playbookGuard = new PlaybookGuard();
    this.#requirePlaybookApproval = opts.requirePlaybookApproval ?? false;
    this.#contextBudget = new ContextBudget(opts.contextBudgetTokens ?? 100_000);
    this.#curatorEnabled = opts.curatorEnabled ?? false;
    this.#curatorConsolidateEveryNGens = opts.curatorConsolidateEveryNGens ?? 3;
    this.#hintStyle = effectiveHintStyle(
      opts.softHintsEnabled ?? false,
      opts.hintStyle ?? "default",
    );
    this.#skillMaxLessons = opts.skillMaxLessons ?? 30;
    this.#deadEndTrackingEnabled = opts.deadEndTrackingEnabled ?? false;
    this.#deadEndMaxEntries = opts.deadEndMaxEntries ?? 20;
    this.#stagnationResetEnabled = opts.stagnationResetEnabled ?? false;
    this.#stagnationDistillTopLessons = opts.stagnationDistillTopLessons ?? 5;
    this.#stagnationDetector = new StagnationDetector({
      rollbackThreshold: opts.stagnationRollbackThreshold,
      plateauWindow: opts.stagnationPlateauWindow,
      plateauEpsilon: opts.stagnationPlateauEpsilon,
    });
    this.#recovery = new GenerationRecovery({
      artifacts: this.#artifactStore,
      scenarioName: this.#scenarioName,
      deadEndTrackingEnabled: this.#deadEndTrackingEnabled,
      deadEndMaxEntries: this.#deadEndMaxEntries,
      stagnationResetEnabled: this.#stagnationResetEnabled,
      stagnationDistillTopLessons: this.#stagnationDistillTopLessons,
      stagnationDetector: this.#stagnationDetector,
    });
    this.#explorationMode = opts.explorationMode ?? "linear";
    this.#experimentalAnnealingEnabled = opts.experimentalAnnealingEnabled ?? false;
    this.#experimentalLevyScoutEnabled = opts.experimentalLevyScoutEnabled ?? false;
    this.#levyScoutAlpha = opts.levyScoutAlpha ?? 1.5;
    this.#levyScoutScale = opts.levyScoutScale ?? 0.2;
    this.#annealingStartTemperature = opts.annealingStartTemperature ?? 0.05;
    this.#annealingEndTemperature = opts.annealingEndTemperature ?? 0.001;
    this.#annealingGenerations = opts.annealingGenerations ?? 20;
    this.#explorationCollapseGuard = opts.explorationCollapseGuard ?? false;
    this.#explorationCollapseAutoMitigation = opts.explorationCollapseAutoMitigation ?? false;
    this.#notifyOn = parseNotificationFilter(opts.notifyOn);
    this.#notifier =
      opts.notifier ?? buildConfiguredNotifier(opts.notifyWebhookUrl ?? null, [...this.#notifyOn]);
    this.#controller = opts.controller ?? null;
    this.#events = opts.events ?? null;
    this.#generationTimeBudgetSeconds = opts.generationTimeBudgetSeconds ?? null;
    this.#hookBus = opts.hookBus ?? new HookBus();
    this.#loadedExtensions = opts.loadedExtensions ?? this.#hookBus.loadedExtensions;
    this.#runtimeSession = opts.runtimeSession;
  }

  async run(runId: RunId, generations: number): Promise<RunResult> {
    this.emitHook(HookEvents.RUN_START, {
      run_id: runId,
      scenario: this.#scenario.name,
      target_generations: generations,
      loaded_extensions: this.#loadedExtensions,
    });
    // Create run record
    this.#store.createRun(runId, this.#scenario.name, generations, "local", this.#agentProvider);
    let orchestration = createGenerationLoopOrchestration({
      runId,
      scenarioName: this.#scenario.name,
      targetGenerations: generations,
      startedAtMs: Date.now(),
    });
    this.#runState = orchestration.runState;
    try {
      this.emit("run_started", orchestration.events.runStarted!);
      this.startTaskPlan(runId);
      this.startProgressNotes(runId);

      while (hasRemainingGenerationCycles(orchestration.cycleState)) {
        orchestration = await this.runGeneration(runId, orchestration);
      }

      this.#controller?.throwIfStopRequested();
      this.publishTaskPlan((taskPlan) =>
        taskPlan.progress({
          activeStepId: "finalize_run",
          completedStepIds: ["prepare_run", "iterate_strategies"],
          summary: "Strategy generations completed; finalizing run artifacts.",
        }),
      );
      return await this.finalizeSuccessfulRun(runId, orchestration);
    } catch (error) {
      const stopRequest = this.resolveStopRequest(error);
      if (stopRequest) {
        return this.handleRunStop(runId, stopRequest);
      }
      return await this.handleRunFailure(runId, orchestration, error);
    }
  }

  private async runGeneration(
    runId: RunId,
    orchestration: GenerationLoopOrchestration,
  ): Promise<GenerationLoopOrchestration> {
    await this.#controller?.waitAtBoundary();
    const generationStartedAtMs = Date.now();
    const generationBudget = new SolveGenerationBudget({
      scenarioName: this.#scenario.name,
      budgetSeconds: this.#generationTimeBudgetSeconds,
    });
    generationBudget.check("generation start");
    const activeGeneration = orchestration.cycleState.completedGenerations + 1;
    this.emitHook(HookEvents.GENERATION_START, {
      run_id: runId,
      scenario: this.#scenario.name,
      generation: activeGeneration,
    });
    let lifecycle: Awaited<ReturnType<typeof runGenerationLifecycleWorkflow>>;
    try {
      lifecycle = await runGenerationLifecycleWorkflow(
        createGenerationLifecycleWorkflow({
          orchestration,
          curatorEnabled: this.#curatorEnabled,
          maxRetries: this.#maxRetries,
          onEvent: (event) => this.emit(event.event, event.payload),
          runAttempt: ({ attemptOrchestration, generation, onEvent }) =>
            this.runGenerationAttempt(attemptOrchestration, runId, generation, onEvent),
        }),
      );
      generationBudget.check("generation lifecycle");
      orchestration = lifecycle.orchestration;
      this.#runState = orchestration.runState;
      const persistenceStartedAt = Date.now();
      this.emit("persistence_started", { run_id: runId, generation: lifecycle.generation });
      this.#journal.persistGeneration(runId, lifecycle.generation, lifecycle.finalizedAttempt);
      this.emit("persistence_completed", {
        run_id: runId,
        generation: lifecycle.generation,
        duration_ms: Date.now() - persistenceStartedAt,
      });
      generationBudget.check("generation persistence");
      await this.#controller?.waitAtBoundary();
      await this.runSupportRoles(runId, lifecycle.generation, lifecycle.finalizedAttempt);
      generationBudget.check("support roles");
      await this.#controller?.waitAtBoundary();
      await this.applyAdvancedFeatures(
        runId,
        lifecycle.generation,
        lifecycle.finalizedAttempt,
        lifecycle.phaseState.previousBestForGeneration,
      );
      generationBudget.check("advanced generation features");
      const elapsedSeconds = Math.max(0, (Date.now() - generationStartedAtMs) / 1_000);
      this.#journal.persistGenerationTiming(
        runId,
        lifecycle.generation,
        lifecycle.finalizedAttempt,
        elapsedSeconds,
      );
      await this.#controller?.waitAtBoundary();
      lifecycle = completeGenerationLifecycleWorkflow(lifecycle);
      orchestration = lifecycle.orchestration;
      this.emit("generation_timing", {
        run_id: runId,
        generation: lifecycle.generation,
        elapsed_seconds: elapsedSeconds,
      });
      this.emit("generation_completed", orchestration.events.generationCompleted!);
      this.publishProgressNote({
        generation: lifecycle.generation,
        kind: "discovery",
        text: `Generation ${lifecycle.generation} completed with a best score of ${lifecycle.finalizedAttempt.tournamentResult.bestScore.toFixed(3)}.`,
      });
    } catch (error) {
      const stopRequest = this.resolveStopRequest(error);
      this.emitHook(HookEvents.GENERATION_END, {
        run_id: runId,
        scenario: this.#scenario.name,
        generation: activeGeneration,
        status: stopRequest ? "stopped" : "failed",
        ...(stopRequest ? {} : { error: error instanceof Error ? error.message : String(error) }),
      });
      throw stopRequest ?? error;
    }
    this.emitHook(HookEvents.GENERATION_END, {
      run_id: runId,
      scenario: this.#scenario.name,
      generation: lifecycle.generation,
      status: "completed",
      mean_score: lifecycle.finalizedAttempt.tournamentResult.meanScore,
      best_score: lifecycle.finalizedAttempt.tournamentResult.bestScore,
      elo: lifecycle.finalizedAttempt.tournamentResult.elo,
      gate_decision: lifecycle.finalizedAttempt.gateDecision,
    });
    return orchestration;
  }

  private async runGenerationAttempt(
    attemptOrchestration: GenerationAttemptOrchestration,
    runId: RunId,
    generation: number,
    onEvent?: (event: GenerationLoopEventSequenceItem) => void,
  ): Promise<{
    attemptOrchestration: GenerationAttemptOrchestration;
    events: GenerationLoopEventSequenceItem[];
  }> {
    await this.#controller?.waitAtBoundary();
    const competitorInput = this.buildCompetitorInput(runId, generation);
    const competitorPrompt = competitorInput.prompt;
    const strategyInterface = competitorInput.strategyInterface;
    this.publishTaskPlan((taskPlan) =>
      taskPlan.progress({
        activeStepId: "iterate_strategies",
        completedStepIds: ["prepare_run"],
        stepDetails: {
          iterate_strategies: {
            detail: `Working on strategy generation ${generation}`,
          },
        },
      }),
    );
    return runGenerationAttemptWorkflow(
      createGenerationAttemptWorkflow({
        attemptOrchestration,
        runId,
        generation,
        competitorPrompt,
        strategyInterface,
        seedBase: this.#seedBase,
        matchesPerGeneration: this.#matchesPerGeneration,
        currentElo: this.#runState!.currentElo,
        roleMetadata: {
          provider: this.providerForRole("competitor").name,
          model: this.modelForRole("competitor"),
          inputBytes: Buffer.byteLength(competitorPrompt, "utf-8"),
        },
        executeCompetitor: () =>
          this.completeRole("competitor", competitorPrompt, "", competitorInput.imageAttachments),
        repairCompetitor: ({ repairPrompt }) =>
          this.completeRole(
            "competitor",
            repairPrompt,
            "You repair malformed strategy output. Return one valid JSON object only.",
            [],
            COMPETITOR_REPAIR_MAX_OUTPUT_TOKENS,
          ),
        ...(onEvent ? { onEvent } : {}),
        beforeTournament: async () => {
          await this.#controller?.waitAtBoundary();
        },
        executeTournament: ({ strategy: nextStrategy, tournamentOptions }) =>
          new TournamentRunner(this.#scenario, tournamentOptions).run(nextStrategy),
        decideGate: ({ attemptOrchestration: currentAttemptOrchestration, tournamentResult }) => {
          const retryCount = currentAttemptOrchestration.phaseState.attemptState.retryCount;
          const baseDecision = this.#gate.evaluate(
            currentAttemptOrchestration.orchestration.cycleState.previousBestOverall,
            tournamentResult.bestScore,
            retryCount,
            this.#maxRetries,
          );
          const decision = applyAnnealingToGateDecision(baseDecision, {
            enabled: this.#experimentalAnnealingEnabled,
            generation,
            randomValue: deterministicAnnealingRandomValue(this.#seedBase, generation, retryCount),
            startTemperature: this.#annealingStartTemperature,
            endTemperature: this.#annealingEndTemperature,
            generations: this.#annealingGenerations,
          });
          const gateDecision =
            (this.#controller?.takeGateOverride() as GenerationAttempt["gateDecision"] | null) ??
            decision.decision;
          return {
            gateDecision,
            delta: decision.delta,
            threshold: decision.threshold,
            metadata: decision.metadata,
          };
        },
      }),
    );
  }

  private async finalizeSuccessfulRun(
    runId: RunId,
    orchestration: GenerationLoopOrchestration,
  ): Promise<RunResult> {
    const sessionReportPath = this.#journal.persistSessionReport(runId, {
      runStartedAtMs: this.#runState!.startedAtMs,
      explorationMode: this.#explorationMode,
    });
    orchestration = completeGenerationLoopRun(orchestration, {
      finishedAtMs: Date.now(),
      sessionReportPath,
      deadEndsFound: this.#journal.countDeadEnds(),
    });
    this.#runState = orchestration.runState;
    this.emitHook(HookEvents.RUN_END, {
      run_id: runId,
      scenario: this.#scenario.name,
      status: "completed",
      completed_generations: orchestration.cycleState.completedGenerations,
      best_score: this.#runState.bestScore,
      elo: this.#runState.currentElo,
      session_report_path: sessionReportPath,
      dead_ends_found: this.#journal.countDeadEnds(),
    });
    this.#store.updateRunStatus(runId, "completed");
    this.finishTaskPlan("completed", "Strategy run completed.");
    this.publishProgressNote({
      generation: orchestration.cycleState.completedGenerations,
      kind: "verification",
      text: `Verified ${orchestration.cycleState.completedGenerations} completed generations with a best score of ${this.#runState.bestScore.toFixed(3)}.`,
    });
    this.emit("run_completed", orchestration.events.runCompleted!);
    await this.notify("completion", runId, this.#runState.bestScore, {
      roundCount: orchestration.cycleState.completedGenerations,
      metadata: { session_report_path: sessionReportPath },
    });

    return {
      runId,
      generationsCompleted: orchestration.cycleState.completedGenerations,
      bestScore: this.#runState.bestScore,
      currentElo: this.#runState.currentElo,
    };
  }

  private handleRunStop(runId: RunId, stopRequest: RunStopRequestedError): never {
    const trajectory = this.#store.getScoreTrajectory(runId);
    const bestScore = trajectory.reduce(
      (best, row) => Math.max(best, row.best_score),
      Number.NEGATIVE_INFINITY,
    );
    const progress = {
      completedGenerations: trajectory.length,
      ...(Number.isFinite(bestScore) ? { bestScore } : {}),
    };
    this.emitResolvedTerminalHook(HookEvents.RUN_END, {
      run_id: runId,
      scenario: this.#scenario.name,
      status: "stopped",
      completed_generations: progress.completedGenerations,
      ...(progress.bestScore === undefined ? {} : { best_score: progress.bestScore }),
      elo: this.#runState?.currentElo ?? 1000,
    });
    this.#store.updateRunStatus(runId, "stopped");
    this.finishTaskPlan("interrupted", "Strategy run was interrupted.");
    throw stopRequest.withProgress(progress);
  }

  private async handleRunFailure(
    runId: RunId,
    orchestration: GenerationLoopOrchestration,
    error: unknown,
  ): Promise<never> {
    orchestration = failGenerationLoopRun(orchestration, {
      finishedAtMs: Date.now(),
      error: error instanceof Error ? error.message : String(error),
    });
    this.#runState = orchestration.runState;
    this.emitResolvedTerminalHook(HookEvents.RUN_END, {
      run_id: runId,
      scenario: this.#scenario.name,
      status: "failed",
      completed_generations: this.#store.getScoreTrajectory(runId).length,
      best_score: this.#runState.bestScore,
      elo: this.#runState.currentElo,
      error: error instanceof Error ? error.message : String(error),
    });
    this.#store.updateRunStatus(runId, "failed");
    this.finishTaskPlan("failed", "Strategy run failed before completion.");
    this.publishProgressNote({
      generation: orchestration.cycleState.completedGenerations,
      kind: "blocker",
      text: "The strategy run could not complete; retained progress remains available for review.",
    });
    this.emit("run_failed", orchestration.events.runFailed!);
    await this.notify("failure", runId, this.#runState.bestScore, {
      roundCount: this.#store.getScoreTrajectory(runId).length,
      error: error instanceof Error ? error.message : String(error),
    });
    throw error;
  }

  private resolveStopRequest(error: unknown): RunStopRequestedError | null {
    if (isRunStopRequestedError(error)) return error;
    return this.#controller?.getStopRequest() ?? null;
  }

  private buildCompetitorInput(
    runId: RunId,
    generation: number,
  ): {
    prompt: string;
    strategyInterface: string;
    imageAttachments: readonly ValidatedImageAttachment[];
  } {
    const consumedHint = consumeFreshStartHint(this.#runState!);
    this.#runState = consumedHint.state;
    const freshStartHint = consumedHint.hint;
    const scoutHint = renderLevyScoutGuidance({
      enabled: this.#experimentalLevyScoutEnabled,
      seedBase: this.#seedBase,
      generation,
      alpha: this.#levyScoutAlpha,
      scale: this.#levyScoutScale,
    });
    const contextComponents = this.applyContextComponentsHook(runId, generation, "competitor", {
      playbook: this.#artifactStore.readPlaybook(this.#scenarioName),
      trajectory: new ScoreTrajectoryBuilder(this.#store.getScoreTrajectory(runId)).build(),
      session_reports: this.#artifactStore.readSessionReports(this.#scenarioName),
      scout_mutation_guidance: scoutHint,
    });
    const compacted = this.compactPromptComponentsForRun(runId, generation, contextComponents);
    const trimmed = this.#contextBudget.apply({
      ...compacted,
      dead_ends: this.#artifactStore.readDeadEnds(this.#scenarioName),
    });
    const injectedHint = this.#controller?.takeHintInput();
    const operatorHint =
      [trimmed.scout_mutation_guidance, injectedHint?.text].filter(Boolean).join("\n\n") || null;

    const strategyInterface = this.#scenario.describeStrategyInterface();
    const competitor = buildCompetitorPrompt({
      scenarioName: this.#scenario.name,
      scenarioRules: this.#scenario.describeRules(),
      strategyInterface,
      evaluationCriteria: this.#scenario.describeEvaluationCriteria(),
      playbook: trimmed.playbook,
      trajectory: trimmed.trajectory,
      deadEnds: trimmed.dead_ends,
      sessionReports: trimmed.session_reports,
      freshStartHint,
      operatorHint,
    });
    return {
      prompt: this.applyContextHook(runId, generation, { competitor }).competitor ?? competitor,
      strategyInterface,
      imageAttachments: injectedHint?.imageAttachments ?? [],
    };
  }

  private applyContextComponentsHook(
    runId: RunId,
    generation: number,
    role: string,
    components: Record<string, string>,
  ): Record<string, string> {
    const event = this.emitHook(HookEvents.CONTEXT_COMPONENTS, {
      run_id: runId,
      scenario: this.#scenario.name,
      generation,
      role,
      components,
    });
    return readStringRecord(event.payload.components) ?? components;
  }

  private compactPromptComponentsForRun(
    runId: RunId,
    generation: number,
    components: Record<string, string>,
  ): Record<string, string> {
    const before = this.emitHook(HookEvents.BEFORE_COMPACTION, {
      run_id: runId,
      scenario: this.#scenario.name,
      generation,
      components,
      semantic_compaction: true,
    });
    const inputComponents = readStringRecord(before.payload.components) ?? components;
    const compacted = compactPromptComponents(inputComponents);
    const after = this.emitHook(HookEvents.AFTER_COMPACTION, {
      run_id: runId,
      scenario: this.#scenario.name,
      generation,
      input_components: inputComponents,
      components: compacted,
      semantic_compaction: true,
    });
    const finalComponents = readStringRecord(after.payload.components) ?? compacted;
    const entries = compactionEntriesForComponents(inputComponents, finalComponents, {
      context: {
        scenario: this.#scenario.name,
        run_id: runId,
        generation,
      },
      parentId: this.#artifactStore.latestCompactionEntryId(runId),
    });
    if (entries.length > 0) {
      const ledgerWrite = this.#artifactStore.appendCompactionEntries(runId, entries);
      this.#runtimeSession?.recordCompaction({
        runId,
        generation,
        ledgerPath: ledgerWrite?.ledgerPath ?? this.#artifactStore.compactionLedgerPath(runId),
        latestEntryPath:
          ledgerWrite?.latestEntryPath ?? this.#artifactStore.compactionLatestEntryPath(runId),
        entries: ledgerWrite?.entries ?? entries,
      });
    }
    return finalComponents;
  }

  private buildSupportPrompt(
    role: "analyst" | "coach",
    runId: RunId,
    generation: number,
    attempt: GenerationAttempt,
  ): string {
    const trimmed = this.#contextBudget.apply({
      playbook: this.#artifactStore.readPlaybook(this.#scenarioName),
      trajectory: new ScoreTrajectoryBuilder(this.#store.getScoreTrajectory(runId)).build(),
      analysis:
        `Gate decision: ${attempt.gateDecision}\n` +
        `Best score: ${attempt.tournamentResult.bestScore.toFixed(4)}\n` +
        `Mean score: ${attempt.tournamentResult.meanScore.toFixed(4)}\n` +
        `Wins/Losses: ${attempt.tournamentResult.wins}/${attempt.tournamentResult.losses}`,
      dead_ends: this.#artifactStore.readDeadEnds(this.#scenarioName),
    });

    const prompt = buildSupportPrompt({
      role,
      scenarioName: this.#scenario.name,
      scenarioRules: this.#scenario.describeRules(),
      strategyInterface: this.#scenario.describeStrategyInterface(),
      strategyJson: attempt.strategy,
      analysisSummary: trimmed.analysis,
      playbook: trimmed.playbook,
      trajectory: trimmed.trajectory,
      deadEnds: trimmed.dead_ends,
      hintStyle: this.#hintStyle,
    });
    return this.applyContextHook(runId, generation, { [role]: prompt })[role] ?? prompt;
  }

  private buildCuratorPrompt(
    runId: RunId,
    currentPlaybook: string,
    proposedPlaybook: string,
    attempt: GenerationAttempt,
  ): string {
    const trajectory = new ScoreTrajectoryBuilder(this.#store.getScoreTrajectory(runId)).build();

    return buildCuratorPrompt({
      tournamentSummary: `Gate=${attempt.gateDecision}, Best=${attempt.tournamentResult.bestScore.toFixed(4)}, Mean=${attempt.tournamentResult.meanScore.toFixed(4)}`,
      currentPlaybook,
      proposedPlaybook,
      trajectory,
      hintStyle: this.#hintStyle,
    });
  }

  private buildCuratorConsolidationPrompt(lessons: string): string {
    return buildCuratorConsolidationPrompt({
      lessons,
      skillMaxLessons: this.#skillMaxLessons,
    });
  }

  private providerForRole(role: GenerationRole): LLMProvider {
    return this.#roleProviders[role] ?? this.#provider;
  }

  private modelForRole(role: GenerationRole): string | undefined {
    return this.#roleModels[role];
  }

  private async completeRole(
    role: GenerationRole,
    userPrompt: string,
    systemPrompt = "",
    imageAttachments: readonly ValidatedImageAttachment[] = [],
    maxTokens?: number,
  ): Promise<CompletionResult> {
    const provider = this.providerForRole(role);
    const model = this.modelForRole(role);
    assertProviderSupportsImageAttachments(provider, model, imageAttachments);
    return completeWithProviderHooks({
      hookBus: this.#hookBus,
      provider,
      role,
      model,
      systemPrompt,
      userPrompt,
      imageAttachments,
      maxTokens,
    });
  }

  private async runSupportRoles(
    runId: RunId,
    gen: number,
    attempt: GenerationAttempt,
  ): Promise<void> {
    const analystPrompt = this.buildSupportPrompt("analyst", runId, gen, attempt);
    const coachPrompt = this.buildSupportPrompt("coach", runId, gen, attempt);
    this.emit("role_started", this.buildRoleStartedPayload(runId, gen, "analyst", analystPrompt));
    this.emit("role_started", this.buildRoleStartedPayload(runId, gen, "coach", coachPrompt));
    const analystStartedAt = Date.now();
    const coachStartedAt = Date.now();
    const [analystResult, coachResult] = await Promise.all([
      this.completeRole("analyst", analystPrompt),
      this.completeRole("coach", coachPrompt),
    ]);
    this.emitRoleCompleted(
      runId,
      gen,
      "analyst",
      analystStartedAt,
      analystResult.usage,
      analystPrompt,
      analystResult.model ?? undefined,
    );
    this.emitRoleCompleted(
      runId,
      gen,
      "coach",
      coachStartedAt,
      coachResult.usage,
      coachPrompt,
      coachResult.model ?? undefined,
    );

    this.#store.appendAgentOutput(runId, gen, "analyst", analystResult.text);
    this.#store.appendAgentOutput(runId, gen, "coach", coachResult.text);

    const generationDir = this.#artifactStore.generationDir(runId, gen);
    this.#artifactStore.writeMarkdown(join(generationDir, "analyst.md"), analystResult.text);
    this.#artifactStore.writeMarkdown(join(generationDir, "coach.md"), coachResult.text);
    this.#artifactStore.appendMarkdown(
      join(this.#artifactStore.runsRoot, runId, "support_log.md"),
      analystResult.text,
      `Generation ${gen} Analyst`,
    );
    this.#artifactStore.appendMarkdown(
      join(this.#artifactStore.runsRoot, runId, "support_log.md"),
      coachResult.text,
      `Generation ${gen} Coach`,
    );

    const currentPlaybook = this.#artifactStore.readPlaybook(this.#scenarioName);
    const normalizedPlaybook = currentPlaybook === EMPTY_PLAYBOOK_SENTINEL ? "" : currentPlaybook;
    // AC-932: which markers are missing, not merely whether any are. A dropped
    // playbook update is the loop failing to learn from a generation, and the
    // operator can only fix it if they can see which half of the contract the
    // model broke.
    const missingMarkers = missingPlaybookMarkers(coachResult.text);
    const hasStructuredPlaybook = missingMarkers.length === 0;
    const playbookCheck = this.#playbookGuard.check(normalizedPlaybook, coachResult.text);

    let nextPlaybook = "";
    if (hasStructuredPlaybook && playbookCheck.approved) {
      nextPlaybook = coachResult.text;
    } else {
      // Two different things used to look identical here. Format drift is a
      // model or configuration problem the operator can act on; a guard
      // rejection is the guard doing its job. Reporting them as one silent
      // no-op made the first indistinguishable from the second, and neither
      // visible at all.
      const payload: PlaybookUpdateSkippedPayload = {
        run_id: runId,
        scenario: this.#scenario.name,
        generation: gen,
        reason: hasStructuredPlaybook ? "guard_rejected" : "missing_markers",
        missing_markers: missingMarkers,
        ...(hasStructuredPlaybook && playbookCheck.reason
          ? { guard_reason: playbookCheck.reason }
          : {}),
      };
      this.emit(PLAYBOOK_UPDATE_SKIPPED_EVENT, payload);
    }

    if (nextPlaybook && this.#curatorEnabled && normalizedPlaybook) {
      this.emit("curator_started", { run_id: runId, generation: gen });
      const curatorStartedAt = Date.now();
      const curatorResult = await this.completeRole(
        "curator",
        this.buildCuratorPrompt(runId, normalizedPlaybook, nextPlaybook, attempt),
      );
      this.emitRoleCompleted(runId, gen, "curator", curatorStartedAt, curatorResult.usage);
      this.#store.appendAgentOutput(runId, gen, "curator", curatorResult.text);
      this.#artifactStore.writeMarkdown(join(generationDir, "curator.md"), curatorResult.text);
      this.#artifactStore.appendMarkdown(
        join(this.#artifactStore.runsRoot, runId, "support_log.md"),
        curatorResult.text,
        `Generation ${gen} Curator`,
      );

      const curatorDecision = parseCuratorPlaybookDecision(curatorResult.text);
      if (curatorDecision.decision === "reject") {
        nextPlaybook = "";
      } else if (curatorDecision.decision === "merge" && curatorDecision.playbook) {
        nextPlaybook = curatorDecision.playbook;
      }
      this.emit("curator_completed", {
        run_id: runId,
        generation: gen,
        decision: curatorDecision.decision,
      });
    }

    if (nextPlaybook) {
      const playbookResult = this.#artifactStore.writeOrStagePlaybook(
        this.#scenarioName,
        nextPlaybook,
        {
          requireApproval: this.#requirePlaybookApproval,
          sourceRunId: runId,
          generation: gen,
          curatorDecision: "advance",
        },
      );
      if (playbookResult === "pending") {
        this.emit("playbook_pending", {
          run_id: runId,
          scenario: this.#scenario.name,
          generation: gen,
        });
      }
    }

    if (
      this.#curatorEnabled &&
      this.#curatorConsolidateEveryNGens > 0 &&
      gen % this.#curatorConsolidateEveryNGens === 0
    ) {
      await this.runCuratorConsolidation(runId, gen);
    }
  }

  private async runCuratorConsolidation(runId: RunId, gen: number): Promise<void> {
    if (this.#requirePlaybookApproval) return;
    const playbook = this.#artifactStore.readPlaybook(this.#scenarioName);
    if (!playbook || playbook === EMPTY_PLAYBOOK_SENTINEL) return;

    const lessons = extractMarkedSection(
      playbook,
      PLAYBOOK_MARKERS.LESSONS_START,
      PLAYBOOK_MARKERS.LESSONS_END,
    );
    if (!lessons.trim()) return;

    const result = await this.completeRole(
      "curator",
      this.buildCuratorConsolidationPrompt(lessons),
    );
    this.#store.appendAgentOutput(runId, gen, "curator_consolidation", result.text);
    this.#artifactStore.writeMarkdown(
      join(this.#artifactStore.generationDir(runId, gen), "curator_consolidation.md"),
      result.text,
    );
    this.#artifactStore.appendMarkdown(
      join(this.#artifactStore.runsRoot, runId, "support_log.md"),
      result.text,
      `Generation ${gen} Curator Consolidation`,
    );

    const parsed = parseCuratorLessonResult(result.text);
    if (!parsed.consolidatedLessons.trim()) return;

    const updatedPlaybook = replaceMarkedSection(
      playbook,
      PLAYBOOK_MARKERS.LESSONS_START,
      PLAYBOOK_MARKERS.LESSONS_END,
      parsed.consolidatedLessons,
    );
    this.#artifactStore.writePlaybook(this.#scenarioName, updatedPlaybook);
  }

  private async applyAdvancedFeatures(
    runId: RunId,
    gen: number,
    attempt: GenerationAttempt,
    previousBestForGeneration: number,
  ): Promise<void> {
    const outcome = this.#recovery.handleAttempt(runId, {
      generation: gen,
      gateDecision: attempt.gateDecision,
      bestScore: attempt.tournamentResult.bestScore,
      strategy: attempt.strategy,
      previousBestForGeneration,
    });

    for (const event of outcome.events) {
      this.emit(event.event, event.payload);
    }

    if (outcome.shouldNotifyRegression) {
      await this.notify("regression", runId, attempt.tournamentResult.bestScore, {
        previousBest: previousBestForGeneration,
        roundCount: gen,
        metadata: { gate_decision: attempt.gateDecision },
      });
    }

    if (outcome.shouldNotifyThreshold) {
      await this.notify("threshold_met", runId, attempt.tournamentResult.bestScore, {
        previousBest: previousBestForGeneration,
        roundCount: gen,
        metadata: { gate_decision: attempt.gateDecision },
      });
    }

    if (outcome.freshStartHint) {
      this.#runState = queueFreshStartHint(this.#runState!, outcome.freshStartHint);
    }
    if (attempt.gateDecision === "rollback" || outcome.freshStartHint) {
      this.publishTaskPlan((taskPlan) =>
        taskPlan.replan({
          activeStepId: "iterate_strategies",
          completedStepIds: ["prepare_run"],
          summary: "Adjusting the strategy approach after a recovery signal.",
          stepDetails: {
            iterate_strategies: {
              detail: `Revising the approach for generation ${gen}`,
            },
          },
        }),
      );
      this.publishProgressNote({
        generation: gen,
        kind: "decision",
        text: "A recovery signal changed the strategy approach for the next attempt.",
      });
    }
    this.persistExplorationCollapseGuard(runId, gen);
  }

  private persistExplorationCollapseGuard(runId: RunId, gen: number): void {
    if (!this.#explorationCollapseGuard) return;
    const playbook = this.#artifactStore.readPlaybook(this.#scenarioName).trim();
    if (!playbook || playbook === EMPTY_PLAYBOOK_SENTINEL) return;
    const snapshots = explorationSnapshots(this.#store.getScoreTrajectory(runId));
    if (snapshots.length < 2) return;
    const changes: GuidanceChange[] = [
      {
        changeId: `playbook-gen-${gen}`,
        generationIndex: gen,
        kind: "playbook_update",
        sourceComponent: "playbook",
        sourceSpan: "playbook.md",
      },
    ];
    const report = detectExplorationCollapse(snapshots, changes, {
      advisoryOnly: !this.#explorationCollapseAutoMitigation,
      autoMitigation: this.#explorationCollapseAutoMitigation,
    });
    if (report.events.length === 0) return;
    this.#artifactStore.writeJson(
      join(this.#artifactStore.generationDir(runId, gen), "exploration_collapse_guard.json"),
      {
        schema_version: report.schemaVersion,
        advisory_only: report.advisoryOnly,
        events: report.records,
      },
    );
  }

  private async notify(
    type: EventType,
    runId: RunId,
    score: number,
    extras: {
      previousBest?: number;
      roundCount?: number;
      error?: string;
      metadata?: Record<string, unknown>;
    } = {},
  ): Promise<void> {
    if (!this.#notifier || !this.#notifyOn.has(type)) return;
    try {
      await this.#notifier.notify({
        type,
        taskName: this.#scenario.name,
        taskId: runId,
        score,
        previousBest: extras.previousBest,
        roundCount: extras.roundCount,
        error: extras.error,
        metadata: extras.metadata,
      });
    } catch {
      // Notifications must never crash the loop.
    }
  }

  private applyContextHook(
    runId: RunId,
    generation: number,
    roles: Record<string, string>,
  ): Record<string, string> {
    const event = this.emitHook(HookEvents.CONTEXT, {
      run_id: runId,
      scenario: this.#scenario.name,
      generation,
      roles,
    });
    return readStringRecord(event.payload.roles) ?? roles;
  }

  private emitHook(
    name: HookEvents,
    payload: Record<string, unknown>,
  ): ReturnType<HookBus["emit"]> {
    const event = this.#hookBus.emit(name, payload);
    event.raiseIfBlocked();
    return event;
  }

  private emitResolvedTerminalHook(name: HookEvents, payload: Record<string, unknown>): void {
    try {
      this.emitHook(name, payload);
    } catch {
      // A hook cannot reclassify an already-resolved failed or stopped run.
    }
  }

  private emit(event: string, payload: Record<string, unknown>): void {
    this.#events?.emit(event, payload);
  }

  private startTaskPlan(runId: RunId): void {
    this.#taskPlan = null;
    this.#taskPlanFinished = false;
    if (!this.#events) {
      return;
    }
    try {
      this.#taskPlan = createAgentTaskPlanPublisher({
        runId,
        steps: BUILT_IN_GAME_PLAN_STEPS,
        events: this.#events,
      });
    } catch {
      this.#taskPlan = null;
    }
    this.publishTaskPlan((taskPlan) =>
      taskPlan.initial({
        activeStepId: "prepare_run",
        summary: "Preparing the strategy run.",
      }),
    );
  }

  private startProgressNotes(runId: RunId): void {
    this.#progressNotes = null;
    if (!this.#events) {
      return;
    }
    try {
      this.#progressNotes = createAgentProgressNotePublisher({
        runId,
        events: this.#events,
      });
    } catch {
      this.#progressNotes = null;
    }
    this.publishProgressNote({
      generation: 0,
      kind: "intent",
      text: "Prepare the run context, evaluate strategy generations, and verify the best result.",
    });
  }

  private publishProgressNote(input: AgentProgressNoteInput): void {
    try {
      this.#progressNotes?.publish(input);
    } catch {
      // Progress-note telemetry must never alter run results.
    }
  }

  private publishTaskPlan(action: (taskPlan: AgentTaskPlanPublisher) => boolean): void {
    if (!this.#taskPlan || this.#taskPlanFinished) {
      return;
    }
    try {
      action(this.#taskPlan);
    } catch {
      // Task-plan telemetry must never alter run results.
    }
  }

  private finishTaskPlan(status: "completed" | "failed" | "interrupted", summary: string): void {
    if (this.#taskPlanFinished) {
      return;
    }
    this.publishTaskPlan((taskPlan) => taskPlan.terminal(status, { summary }));
    this.#taskPlanFinished = true;
  }

  private emitRoleCompleted(
    runId: RunId,
    generation: number,
    role: "competitor" | "analyst" | "coach" | "curator",
    startedAt: number,
    usage: Record<string, number>,
    input = "",
    model?: string,
  ): void {
    this.emit(
      "role_completed",
      buildRoleCompletedPayload(runId, generation, role, Date.now() - startedAt, usage, {
        provider: this.providerForRole(role).name,
        model: model || this.modelForRole(role),
        inputBytes: Buffer.byteLength(input, "utf-8"),
      }),
    );
  }

  private buildRoleStartedPayload(
    runId: RunId,
    generation: number,
    role: "competitor" | "analyst" | "coach" | "curator",
    input: string,
  ): Record<string, unknown> {
    return {
      run_id: runId,
      generation,
      role,
      provider: this.providerForRole(role).name,
      ...(this.modelForRole(role) ? { model: this.modelForRole(role) } : {}),
      input_bytes: Buffer.byteLength(input, "utf-8"),
    };
  }
}

function explorationSnapshots(rows: unknown[]): ExplorationSnapshot[] {
  return rows.flatMap((row): ExplorationSnapshot[] => {
    if (!isRecord(row)) return [];
    const generation = finiteNumber(row.generation_index);
    const score = finiteNumber(row.best_score);
    if (generation === undefined || score === undefined) return [];
    const gate = typeof row.gate_decision === "string" ? row.gate_decision : "";
    return [
      {
        generationIndex: generation,
        responseLength: 1,
        routeSignature: gate || undefined,
        rollbackRate: gate === "rollback" ? 1 : 0,
        score,
      },
    ];
  });
}

function finiteNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function parseNotificationFilter(spec?: string): Set<EventType> {
  const raw = (spec ?? "threshold_met,failure")
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);

  const allowed = new Set<EventType>(["threshold_met", "regression", "completion", "failure"]);
  const parsed = raw.filter((part): part is EventType => allowed.has(part as EventType));
  return new Set(parsed);
}

function buildConfiguredNotifier(
  webhookUrl: string | null,
  eventFilter: EventType[],
): Notifier | null {
  if (!webhookUrl) return null;
  return new CompositeNotifier([new StdoutNotifier(), new HTTPNotifier(webhookUrl)], eventFilter);
}

function extractMarkedSection(content: string, startMarker: string, endMarker: string): string {
  const start = content.indexOf(startMarker);
  const end = content.indexOf(endMarker);
  if (start === -1 || end === -1 || end <= start) return "";
  return content.slice(start + startMarker.length, end).trim();
}

function replaceMarkedSection(
  content: string,
  startMarker: string,
  endMarker: string,
  replacement: string,
): string {
  const start = content.indexOf(startMarker);
  const end = content.indexOf(endMarker);
  if (start === -1 || end === -1 || end <= start) return content;
  return [
    content.slice(0, start + startMarker.length),
    "\n",
    replacement.trim(),
    "\n",
    content.slice(end),
  ].join("");
}

function readStringRecord(value: unknown): Record<string, string> | undefined {
  if (!isRecord(value)) {
    return undefined;
  }
  const result: Record<string, string> = {};
  for (const [key, raw] of Object.entries(value)) {
    if (typeof raw === "string") {
      result[key] = raw;
    }
  }
  return result;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function normalizeAgentProviderName(value: string): string {
  return value.trim().toLowerCase() || "unknown";
}
