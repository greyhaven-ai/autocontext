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

import type { CompletionResult, LLMProvider } from "../types/index.js";
import type { ScenarioInterface } from "../scenarios/game-interface.js";
import type { SQLiteStore } from "../storage/index.js";
import { TournamentRunner } from "../execution/tournament.js";
import { BackpressureGate } from "./backpressure.js";
import { ArtifactStore, EMPTY_PLAYBOOK_SENTINEL } from "../knowledge/artifact-store.js";
import { PlaybookGuard, PLAYBOOK_MARKERS } from "../knowledge/playbook.js";
import { ScoreTrajectoryBuilder } from "../knowledge/trajectory.js";
import { generateSessionReport } from "../knowledge/session-report.js";
import {
  compactPromptComponents,
  compactionEntriesForComponents,
} from "../knowledge/semantic-compaction.js";
import { completeWithProviderHooks, HookEvents, HookBus } from "../extensions/index.js";
import { ContextBudget } from "../prompts/context-budget.js";
import { parseCuratorLessonResult, parseCuratorPlaybookDecision } from "../agents/curator-parser.js";
import {
  CompositeNotifier,
  HTTPNotifier,
  StdoutNotifier,
  type EventType,
  type Notifier,
} from "../notifications/index.js";
import type { LoopController } from "./controller.js";
import type { EventStreamEmitter } from "./events.js";
import { StagnationDetector } from "./stagnation.js";
import {
  buildCompetitorPrompt,
  buildCuratorConsolidationPrompt,
  buildCuratorPrompt,
  buildSupportPrompt,
} from "./generation-prompts.js";
import { createGenerationAttemptWorkflow, runGenerationAttemptWorkflow } from "./generation-attempt-workflow.js";
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
} from "./generation-loop-orchestrator.js";
import { GenerationRecovery } from "./generation-recovery.js";
import { hasRemainingGenerationCycles } from "./generation-cycle-state.js";
import { type GenerationAttempt } from "./generation-phase-state.js";
import {
  consumeFreshStartHint,
  queueFreshStartHint,
  type GenerationRunState,
} from "./generation-run-state.js";
import { join } from "node:path";
import type { GenerationRole } from "../providers/index.js";

export interface GenerationRunnerOpts {
  provider: LLMProvider;
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
  contextBudgetTokens?: number;
  curatorEnabled?: boolean;
  curatorConsolidateEveryNGens?: number;
  skillMaxLessons?: number;
  deadEndTrackingEnabled?: boolean;
  deadEndMaxEntries?: number;
  stagnationResetEnabled?: boolean;
  stagnationRollbackThreshold?: number;
  stagnationPlateauWindow?: number;
  stagnationPlateauEpsilon?: number;
  stagnationDistillTopLessons?: number;
  explorationMode?: string;
  notifyWebhookUrl?: string | null;
  notifyOn?: string;
  notifier?: Notifier | null;
  controller?: LoopController;
  events?: EventStreamEmitter;
  generationTimeBudgetSeconds?: number | null;
  hookBus?: HookBus | null;
  loadedExtensions?: string[];
}

export interface RunResult {
  runId: string;
  generationsCompleted: number;
  bestScore: number;
  currentElo: number;
}

export class GenerationRunner {
  #provider: LLMProvider;
  #roleProviders: Partial<Record<GenerationRole, LLMProvider>>;
  #roleModels: Partial<Record<GenerationRole, string>>;
  #scenario: ScenarioInterface;
  #store: SQLiteStore;
  #artifactStore: ArtifactStore;
  #journal: GenerationJournal;
  #recovery: GenerationRecovery;
  #matchesPerGeneration: number;
  #maxRetries: number;
  #gate: BackpressureGate;
  #seedBase: number;
  #playbookGuard: PlaybookGuard;
  #contextBudget: ContextBudget;
  #curatorEnabled: boolean;
  #curatorConsolidateEveryNGens: number;
  #skillMaxLessons: number;
  #deadEndTrackingEnabled: boolean;
  #deadEndMaxEntries: number;
  #stagnationResetEnabled: boolean;
  #stagnationDistillTopLessons: number;
  #stagnationDetector: StagnationDetector;
  #explorationMode: string;
  #notifier: Notifier | null;
  #notifyOn: Set<EventType>;
  #controller: LoopController | null;
  #events: EventStreamEmitter | null;
  #generationTimeBudgetSeconds: number | null;
  #hookBus: HookBus;
  #loadedExtensions: string[];
  #runState: GenerationRunState | null = null;

  constructor(opts: GenerationRunnerOpts) {
    this.#provider = opts.provider;
    this.#roleProviders = opts.roleProviders ?? {};
    this.#roleModels = opts.roleModels ?? {};
    this.#scenario = opts.scenario;
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
    this.#contextBudget = new ContextBudget(opts.contextBudgetTokens ?? 100_000);
    this.#curatorEnabled = opts.curatorEnabled ?? false;
    this.#curatorConsolidateEveryNGens = opts.curatorConsolidateEveryNGens ?? 3;
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
      scenarioName: this.#scenario.name,
      deadEndTrackingEnabled: this.#deadEndTrackingEnabled,
      deadEndMaxEntries: this.#deadEndMaxEntries,
      stagnationResetEnabled: this.#stagnationResetEnabled,
      stagnationDistillTopLessons: this.#stagnationDistillTopLessons,
      stagnationDetector: this.#stagnationDetector,
    });
    this.#explorationMode = opts.explorationMode ?? "linear";
    this.#notifyOn = parseNotificationFilter(opts.notifyOn);
    this.#notifier =
      opts.notifier
      ?? buildConfiguredNotifier(opts.notifyWebhookUrl ?? null, [...this.#notifyOn]);
    this.#controller = opts.controller ?? null;
    this.#events = opts.events ?? null;
    this.#generationTimeBudgetSeconds = opts.generationTimeBudgetSeconds ?? null;
    this.#hookBus = opts.hookBus ?? new HookBus();
    this.#loadedExtensions = opts.loadedExtensions ?? this.#hookBus.loadedExtensions;
  }

  async run(runId: string, generations: number): Promise<RunResult> {
    this.emitHook(HookEvents.RUN_START, {
      run_id: runId,
      scenario: this.#scenario.name,
      target_generations: generations,
      loaded_extensions: this.#loadedExtensions,
    });
    // Create run record
    this.#store.createRun(runId, this.#scenario.name, generations, "local");
    let orchestration = createGenerationLoopOrchestration({
      runId,
      scenarioName: this.#scenario.name,
      targetGenerations: generations,
      startedAtMs: Date.now(),
    });
    this.#runState = orchestration.runState;
    try {
      this.emit("run_started", orchestration.events.runStarted!);

      while (hasRemainingGenerationCycles(orchestration.cycleState)) {
        await this.#controller?.waitIfPaused();
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
              runAttempt: async ({ attemptOrchestration, generation }) => {
                await this.#controller?.waitIfPaused();
                const competitorPrompt = this.buildCompetitorPrompt(runId, generation);
                return runGenerationAttemptWorkflow(
                  createGenerationAttemptWorkflow({
                    attemptOrchestration,
                    runId,
                    generation,
                    competitorPrompt,
                    seedBase: this.#seedBase,
                    matchesPerGeneration: this.#matchesPerGeneration,
                    currentElo: this.#runState!.currentElo,
                    executeCompetitor: () => this.completeRole("competitor", competitorPrompt),
                    beforeTournament: async () => {
                      await this.#controller?.waitIfPaused();
                    },
                    executeTournament: ({ strategy: nextStrategy, tournamentOptions }) =>
                      new TournamentRunner(this.#scenario, tournamentOptions).run(nextStrategy),
                    decideGate: ({ attemptOrchestration: currentAttemptOrchestration, tournamentResult }) => {
                      const decision = this.#gate.evaluate(
                        currentAttemptOrchestration.orchestration.cycleState.previousBestOverall,
                        tournamentResult.bestScore,
                        currentAttemptOrchestration.phaseState.attemptState.retryCount,
                        this.#maxRetries,
                      );
                      const gateDecision = this.#controller?.takeGateOverride() as GenerationAttempt["gateDecision"] | null ?? decision.decision;
                      return {
                        gateDecision,
                        delta: decision.delta,
                        threshold: decision.threshold,
                      };
                    },
                  }),
                );
              },
            }),
          );
        } catch (error) {
          this.emitHook(HookEvents.GENERATION_END, {
            run_id: runId,
            scenario: this.#scenario.name,
            generation: activeGeneration,
            status: "failed",
            error: error instanceof Error ? error.message : String(error),
          });
          throw error;
        }
        generationBudget.check("generation lifecycle");
        orchestration = lifecycle.orchestration;
        this.#runState = orchestration.runState;
        for (const event of lifecycle.events) {
          this.emit(event.event, event.payload);
        }

        this.#journal.persistGeneration(runId, lifecycle.generation, lifecycle.finalizedAttempt);
        generationBudget.check("generation persistence");
        await this.#controller?.waitIfPaused();
        await this.runSupportRoles(runId, lifecycle.generation, lifecycle.finalizedAttempt);
        generationBudget.check("support roles");
        await this.applyAdvancedFeatures(
          runId,
          lifecycle.generation,
          lifecycle.finalizedAttempt,
          lifecycle.phaseState.previousBestForGeneration,
        );
        generationBudget.check("advanced generation features");
        lifecycle = completeGenerationLifecycleWorkflow(lifecycle);
        orchestration = lifecycle.orchestration;
        this.emit("generation_completed", orchestration.events.generationCompleted!);
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
      }

      this.#store.updateRunStatus(runId, "completed");
      const sessionReportPath = this.#journal.persistSessionReport(runId, {
        runStartedAtMs: this.#runState.startedAtMs,
        explorationMode: this.#explorationMode,
      });
      orchestration = completeGenerationLoopRun(orchestration, {
        finishedAtMs: Date.now(),
        sessionReportPath,
        deadEndsFound: this.#journal.countDeadEnds(),
      });
      this.#runState = orchestration.runState;
      this.emit("run_completed", orchestration.events.runCompleted!);
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
    } catch (error) {
      orchestration = failGenerationLoopRun(orchestration, {
        finishedAtMs: Date.now(),
        error: error instanceof Error ? error.message : String(error),
      });
      this.#runState = orchestration.runState;
      this.#store.updateRunStatus(runId, "failed");
      this.emit("run_failed", orchestration.events.runFailed!);
      this.emitHook(HookEvents.RUN_END, {
        run_id: runId,
        scenario: this.#scenario.name,
        status: "failed",
        completed_generations: this.#store.getScoreTrajectory(runId).length,
        best_score: this.#runState.bestScore,
        elo: this.#runState.currentElo,
        error: error instanceof Error ? error.message : String(error),
      });
      await this.notify("failure", runId, this.#runState.bestScore, {
        roundCount: this.#store.getScoreTrajectory(runId).length,
        error: error instanceof Error ? error.message : String(error),
      });
      throw error;
    }
  }

  private buildCompetitorPrompt(runId: string, generation: number): string {
    const consumedHint = consumeFreshStartHint(this.#runState!);
    this.#runState = consumedHint.state;
    const freshStartHint = consumedHint.hint;
    const contextComponents = this.applyContextComponentsHook(runId, generation, "competitor", {
      playbook: this.#artifactStore.readPlaybook(this.#scenario.name),
      trajectory: new ScoreTrajectoryBuilder(this.#store.getScoreTrajectory(runId)).build(),
      session_reports: this.#artifactStore.readSessionReports(this.#scenario.name),
    });
    const compacted = this.compactPromptComponentsForRun(runId, generation, contextComponents);
    const trimmed = this.#contextBudget.apply({
      ...compacted,
      dead_ends: this.#artifactStore.readDeadEnds(this.#scenario.name),
    });
    const injectedHint = this.#controller?.takeHint();

    const competitor = buildCompetitorPrompt({
      scenarioName: this.#scenario.name,
      scenarioRules: this.#scenario.describeRules(),
      strategyInterface: this.#scenario.describeStrategyInterface(),
      evaluationCriteria: this.#scenario.describeEvaluationCriteria(),
      playbook: trimmed.playbook,
      trajectory: trimmed.trajectory,
      deadEnds: trimmed.dead_ends,
      sessionReports: trimmed.session_reports,
      freshStartHint,
      operatorHint: injectedHint,
    });
    return this.applyContextHook(runId, generation, { competitor }).competitor ?? competitor;
  }

  private applyContextComponentsHook(
    runId: string,
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
    runId: string,
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
      this.#artifactStore.appendCompactionEntries(runId, entries);
    }
    return finalComponents;
  }

  private buildSupportPrompt(
    role: "analyst" | "coach",
    runId: string,
    generation: number,
    attempt: GenerationAttempt,
  ): string {
    const trimmed = this.#contextBudget.apply({
      playbook: this.#artifactStore.readPlaybook(this.#scenario.name),
      trajectory: new ScoreTrajectoryBuilder(this.#store.getScoreTrajectory(runId)).build(),
      analysis:
        `Gate decision: ${attempt.gateDecision}\n` +
        `Best score: ${attempt.tournamentResult.bestScore.toFixed(4)}\n` +
        `Mean score: ${attempt.tournamentResult.meanScore.toFixed(4)}\n` +
        `Wins/Losses: ${attempt.tournamentResult.wins}/${attempt.tournamentResult.losses}`,
      dead_ends: this.#artifactStore.readDeadEnds(this.#scenario.name),
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
    });
    return this.applyContextHook(runId, generation, { [role]: prompt })[role] ?? prompt;
  }

  private buildCuratorPrompt(
    runId: string,
    currentPlaybook: string,
    proposedPlaybook: string,
    attempt: GenerationAttempt,
  ): string {
    const trajectory = new ScoreTrajectoryBuilder(this.#store.getScoreTrajectory(runId)).build();

    return buildCuratorPrompt({
      tournamentSummary:
        `Gate=${attempt.gateDecision}, Best=${attempt.tournamentResult.bestScore.toFixed(4)}, Mean=${attempt.tournamentResult.meanScore.toFixed(4)}`,
      currentPlaybook,
      proposedPlaybook,
      trajectory,
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

  private async completeRole(role: GenerationRole, userPrompt: string, systemPrompt = ""): Promise<CompletionResult> {
    return completeWithProviderHooks({
      hookBus: this.#hookBus,
      provider: this.providerForRole(role),
      role,
      model: this.modelForRole(role),
      systemPrompt,
      userPrompt,
    });
  }


  private async runSupportRoles(
    runId: string,
    gen: number,
    attempt: GenerationAttempt,
  ): Promise<void> {
    const analystStartedAt = Date.now();
    const coachStartedAt = Date.now();
    const [analystResult, coachResult] = await Promise.all([
      this.completeRole("analyst", this.buildSupportPrompt("analyst", runId, gen, attempt)),
      this.completeRole("coach", this.buildSupportPrompt("coach", runId, gen, attempt)),
    ]);
    this.emitRoleCompleted(runId, gen, "analyst", analystStartedAt, analystResult.usage);
    this.emitRoleCompleted(runId, gen, "coach", coachStartedAt, coachResult.usage);

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

    const currentPlaybook = this.#artifactStore.readPlaybook(this.#scenario.name);
    const normalizedPlaybook =
      currentPlaybook === EMPTY_PLAYBOOK_SENTINEL ? "" : currentPlaybook;
    const hasStructuredPlaybook =
      coachResult.text.includes(PLAYBOOK_MARKERS.PLAYBOOK_START) &&
      coachResult.text.includes(PLAYBOOK_MARKERS.PLAYBOOK_END) &&
      coachResult.text.includes(PLAYBOOK_MARKERS.LESSONS_START) &&
      coachResult.text.includes(PLAYBOOK_MARKERS.LESSONS_END) &&
      coachResult.text.includes(PLAYBOOK_MARKERS.HINTS_START) &&
      coachResult.text.includes(PLAYBOOK_MARKERS.HINTS_END);
    const playbookCheck = this.#playbookGuard.check(normalizedPlaybook, coachResult.text);

    let nextPlaybook = "";
    if (hasStructuredPlaybook && playbookCheck.approved) {
      nextPlaybook = coachResult.text;
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
      this.#artifactStore.writePlaybook(this.#scenario.name, nextPlaybook);
    }

    if (
      this.#curatorEnabled
      && this.#curatorConsolidateEveryNGens > 0
      && gen % this.#curatorConsolidateEveryNGens === 0
    ) {
      await this.runCuratorConsolidation(runId, gen);
    }
  }

  private async runCuratorConsolidation(runId: string, gen: number): Promise<void> {
    const playbook = this.#artifactStore.readPlaybook(this.#scenario.name);
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
    this.#artifactStore.writePlaybook(this.#scenario.name, updatedPlaybook);
  }

  private async applyAdvancedFeatures(
    runId: string,
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
  }


  private async notify(
    type: EventType,
    runId: string,
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
    runId: string,
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

  private emitHook(name: HookEvents, payload: Record<string, unknown>): ReturnType<HookBus["emit"]> {
    const event = this.#hookBus.emit(name, payload);
    event.raiseIfBlocked();
    return event;
  }

  private emit(event: string, payload: Record<string, unknown>): void {
    this.#events?.emit(event, payload);
  }

  private emitRoleCompleted(
    runId: string,
    generation: number,
    role: "competitor" | "analyst" | "coach" | "curator",
    startedAt: number,
    usage: Record<string, number>,
  ): void {
    this.emit(
      "role_completed",
      buildRoleCompletedPayload(
        runId,
        generation,
        role,
        Date.now() - startedAt,
        usage,
      ),
    );
  }
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
  return new CompositeNotifier(
    [new StdoutNotifier(), new HTTPNotifier(webhookUrl)],
    eventFilter,
  );
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

function readString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
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
