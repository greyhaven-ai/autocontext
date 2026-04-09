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

import type { LLMProvider } from "../types/index.js";
import type { ScenarioInterface } from "../scenarios/game-interface.js";
import type { SQLiteStore } from "../storage/index.js";
import { TournamentRunner } from "../execution/tournament.js";
import { BackpressureGate } from "./backpressure.js";
import { ArtifactStore, EMPTY_PLAYBOOK_SENTINEL } from "../knowledge/artifact-store.js";
import { PlaybookGuard, PLAYBOOK_MARKERS } from "../knowledge/playbook.js";
import { ScoreTrajectoryBuilder } from "../knowledge/trajectory.js";
import { generateSessionReport } from "../knowledge/session-report.js";
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
import {
  buildAgentsStartedPayload,
  buildGateDecidedPayload,
  buildGenerationCompletedPayload,
  buildGenerationStartedPayload,
  buildRunCompletedPayload,
  buildRunFailedPayload,
  buildRunStartedPayload,
  buildTournamentCompletedPayload,
} from "./generation-event-coordinator.js";
import { GenerationJournal } from "./generation-journal.js";
import { GenerationRecovery } from "./generation-recovery.js";
import {
  completeGenerationCycle,
  createGenerationCycleState,
  getActiveGenerationPhaseState,
  hasRemainingGenerationCycles,
  startNextGenerationCycle,
  updateGenerationCyclePhase,
} from "./generation-cycle-state.js";
import {
  applyGenerationPhaseDecision,
  canContinueGenerationPhase,
  didAdvanceGenerationPhase,
  getFinalizedGenerationPhaseAttempt,
  markAwaitingCompetitorResult,
  markAwaitingTournamentResult,
  type GenerationAttempt,
} from "./generation-phase-state.js";
import {
  completeGenerationRun,
  consumeFreshStartHint,
  createGenerationRunState,
  failGenerationRun,
  queueFreshStartHint,
  recordGenerationResult,
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
  }

  async run(runId: string, generations: number): Promise<RunResult> {
    // Create run record
    this.#store.createRun(runId, this.#scenario.name, generations, "local");
    this.#runState = createGenerationRunState({
      runId,
      scenarioName: this.#scenario.name,
      targetGenerations: generations,
      startedAtMs: Date.now(),
    });
    try {
      this.emit(
        "run_started",
        buildRunStartedPayload({
          runId,
          scenarioName: this.#scenario.name,
          targetGenerations: generations,
        }),
      );

      let cycleState = createGenerationCycleState({
        targetGenerations: generations,
      });

      while (hasRemainingGenerationCycles(cycleState)) {
        await this.#controller?.waitIfPaused();
        cycleState = startNextGenerationCycle(cycleState);
        let phaseState = getActiveGenerationPhaseState(cycleState);
        const gen = phaseState.generation;
        this.emit("generation_started", buildGenerationStartedPayload(runId, gen));
        this.emit(
          "agents_started",
          buildAgentsStartedPayload(runId, gen, this.#curatorEnabled),
        );

        // Retry loop for this generation
        while (canContinueGenerationPhase(phaseState, this.#maxRetries)) {
          await this.#controller?.waitIfPaused();
          phaseState = markAwaitingCompetitorResult(phaseState);
          const competitorPrompt = this.buildCompetitorPrompt(runId);

          // Step 1: Get strategy from provider (competitor role)
          const competitorStartedAt = Date.now();
          const competitorResult = await this.completeRole("competitor", competitorPrompt);
          this.emitRoleCompleted("competitor", competitorStartedAt, competitorResult.usage);

          let strategy: Record<string, unknown>;
          try {
            strategy = JSON.parse(competitorResult.text);
          } catch {
            strategy = { aggression: 0.5, defense: 0.5, path_bias: 0.5 };
          }

          // Step 2: Run tournament
          await this.#controller?.waitIfPaused();
          phaseState = markAwaitingTournamentResult(phaseState);
          const seedForGen = this.#seedBase + (gen - 1) * this.#matchesPerGeneration;
          const tournament = new TournamentRunner(this.#scenario, {
            matchCount: this.#matchesPerGeneration,
            seedBase: seedForGen,
            initialElo: this.#runState.currentElo,
          });
          this.emit("tournament_started", {
            run_id: runId,
            generation: gen,
            matches: this.#matchesPerGeneration,
          });
          const tournamentResult = tournament.run(strategy);
          tournamentResult.matches.forEach((match, matchIndex) => {
            this.emit("match_completed", {
              run_id: runId,
              generation: gen,
              match_index: matchIndex,
              score: match.score,
              winner: match.winner ?? "",
            });
          });
          this.emit(
            "tournament_completed",
            buildTournamentCompletedPayload(runId, gen, tournamentResult),
          );

          // Step 3: Backpressure gate
          const decision = this.#gate.evaluate(
            cycleState.previousBestOverall,
            tournamentResult.bestScore,
            phaseState.attemptState.retryCount,
            this.#maxRetries,
          );
          const gateDecision = this.#controller?.takeGateOverride() as GenerationAttempt["gateDecision"] | null ?? decision.decision;
          const attempt: GenerationAttempt = {
            competitorPrompt,
            competitorResultText: competitorResult.text,
            strategy,
            tournamentResult,
            gateDecision,
          };
          this.emit(
            "gate_decided",
            buildGateDecidedPayload(
              runId,
              gen,
              gateDecision,
              decision.delta,
              decision.threshold,
            ),
          );

          phaseState = applyGenerationPhaseDecision(phaseState, attempt);
          cycleState = updateGenerationCyclePhase(cycleState, phaseState);

          if (didAdvanceGenerationPhase(phaseState)) {
            this.#runState = recordGenerationResult(this.#runState!, {
              generation: gen,
              bestScore: tournamentResult.bestScore,
              elo: tournamentResult.elo,
            });
          }
        }

        const finalizedAttempt = getFinalizedGenerationPhaseAttempt(phaseState);

        this.#journal.persistGeneration(runId, gen, finalizedAttempt);
        await this.#controller?.waitIfPaused();
        await this.runSupportRoles(runId, gen, finalizedAttempt);
        await this.applyAdvancedFeatures(runId, gen, finalizedAttempt, phaseState.previousBestForGeneration);
        this.emit(
          "generation_completed",
          buildGenerationCompletedPayload(runId, gen, {
            meanScore: finalizedAttempt.tournamentResult.meanScore,
            bestScore: finalizedAttempt.tournamentResult.bestScore,
            elo: finalizedAttempt.tournamentResult.elo,
            gateDecision: finalizedAttempt.gateDecision,
          }),
        );
        cycleState = completeGenerationCycle(updateGenerationCyclePhase(cycleState, phaseState));
      }

      this.#store.updateRunStatus(runId, "completed");
      this.#runState = completeGenerationRun(this.#runState!, {
        finishedAtMs: Date.now(),
      });
      const sessionReportPath = this.#journal.persistSessionReport(runId, {
        runStartedAtMs: this.#runState.startedAtMs,
        explorationMode: this.#explorationMode,
      });
      this.emit(
        "run_completed",
        buildRunCompletedPayload({
          runId,
          completedGenerations: cycleState.completedGenerations,
          bestScore: this.#runState.bestScore,
          currentElo: this.#runState.currentElo,
          sessionReportPath,
          deadEndsFound: this.#journal.countDeadEnds(),
        }),
      );
      await this.notify("completion", runId, this.#runState.bestScore, {
        roundCount: cycleState.completedGenerations,
        metadata: { session_report_path: sessionReportPath },
      });

      return {
        runId,
        generationsCompleted: cycleState.completedGenerations,
        bestScore: this.#runState.bestScore,
        currentElo: this.#runState.currentElo,
      };
    } catch (error) {
      this.#runState = failGenerationRun(this.#runState!, {
        finishedAtMs: Date.now(),
        error: error instanceof Error ? error.message : String(error),
      });
      this.#store.updateRunStatus(runId, "failed");
      this.emit(
        "run_failed",
        buildRunFailedPayload(
          runId,
          error instanceof Error ? error.message : String(error),
        ),
      );
      await this.notify("failure", runId, this.#runState.bestScore, {
        roundCount: this.#store.getScoreTrajectory(runId).length,
        error: error instanceof Error ? error.message : String(error),
      });
      throw error;
    }
  }

  private buildCompetitorPrompt(runId: string): string {
    const consumedHint = consumeFreshStartHint(this.#runState!);
    this.#runState = consumedHint.state;
    const freshStartHint = consumedHint.hint;
    const trimmed = this.#contextBudget.apply({
      playbook: this.#artifactStore.readPlaybook(this.#scenario.name),
      trajectory: new ScoreTrajectoryBuilder(this.#store.getScoreTrajectory(runId)).build(),
      dead_ends: this.#artifactStore.readDeadEnds(this.#scenario.name),
      session_reports: this.#artifactStore.readSessionReports(this.#scenario.name),
    });
    const injectedHint = this.#controller?.takeHint();

    return buildCompetitorPrompt({
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
  }

  private buildSupportPrompt(
    role: "analyst" | "coach",
    runId: string,
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

    return buildSupportPrompt({
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

  private completeRole(role: GenerationRole, userPrompt: string, systemPrompt = "") {
    return this.providerForRole(role).complete({
      systemPrompt,
      userPrompt,
      model: this.modelForRole(role),
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
      this.completeRole("analyst", this.buildSupportPrompt("analyst", runId, attempt)),
      this.completeRole("coach", this.buildSupportPrompt("coach", runId, attempt)),
    ]);
    this.emitRoleCompleted("analyst", analystStartedAt, analystResult.usage);
    this.emitRoleCompleted("coach", coachStartedAt, coachResult.usage);

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
      this.emitRoleCompleted("curator", curatorStartedAt, curatorResult.usage);
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

  private emit(event: string, payload: Record<string, unknown>): void {
    this.#events?.emit(event, payload);
  }

  private emitRoleCompleted(
    role: "competitor" | "analyst" | "coach" | "curator",
    startedAt: number,
    usage: Record<string, number>,
  ): void {
    const inputTokens = usage.input_tokens ?? usage.inputTokens ?? 0;
    const outputTokens = usage.output_tokens ?? usage.outputTokens ?? 0;
    this.emit("role_completed", {
      role,
      latency_ms: Date.now() - startedAt,
      tokens: inputTokens + outputTokens,
    });
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
