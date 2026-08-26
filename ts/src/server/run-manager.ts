/**
 * Run manager — manages run lifecycle for interactive server (AC-347 Task 26).
 * Mirrors Python's autocontext/server/run_manager.py.
 */

import { dirname, join } from "node:path";
import type { AppSettings } from "../config/index.js";
import { IMAGE_ATTACHMENTS_CAPABILITY, type ValidatedImageAttachment } from "../types/index.js";
import { LoopController } from "../loop/controller.js";
import { EventStreamEmitter } from "../loop/events.js";
import type { EventCallback } from "../loop/events.js";
import type {
  GenerationRole,
  ProviderRuntimeSessionOpts,
  RoleProviderBundle,
} from "../providers/index.js";
import { providerSupportsImageAttachments } from "../providers/index.js";
import { runtimeSessionIdForRun } from "../session/runtime-session-ids.js";
import type { ScenarioPreviewInfo } from "../scenarios/draft-workflow.js";
import type { ImprovementTaskContract } from "../scenarios/improvement-task-contract.js";
import type { TaskDataSourceContent } from "../scenarios/task-data-source.js";
import {
  InteractiveScenarioSession,
  type InteractiveScenarioReadyInfo,
  type InteractiveScenarioScope,
} from "./interactive-scenario-session.js";
import { readScenarioFamily } from "../scenarios/codegen/loader.js";
import { SCENARIO_REGISTRY } from "../scenarios/registry.js";
import { loadSettings } from "../config/index.js";
import {
  buildQueuedRunStatePatch,
  createManagedRunExecution,
} from "./active-run-lifecycle.js";
import {
  buildRunEventStatePatch,
  isTerminalRunPhase,
  mergeRunManagerState,
  notifyRunStateSubscribers,
} from "./run-state-workflow.js";
import { buildEnvironmentInfo } from "./run-environment-catalog.js";
import { executeChatAgentInteraction } from "./chat-agent-workflow.js";
import { RunCustomScenarioRegistry } from "./run-custom-scenario-registry.js";
import { RunManagerProviderSession } from "./run-manager-provider-session.js";
import {
  executeAgentTaskCustomStartRun,
  executeBuiltInGameStartRun,
  executeGeneratedCustomStartRun,
  resolveBuiltInGameScenario,
  resolveRunStartPlan,
} from "./run-start-workflow.js";
import { createRuntimeSessionEventStreamSink } from "./runtime-session-event-stream.js";

export interface RunManagerOpts {
  dbPath: string;
  migrationsDir: string;
  runsRoot: string;
  knowledgeRoot: string;
  skillsRoot?: string;
  providerType?: string;
  apiKey?: string;
  baseUrl?: string;
  model?: string;
  deps?: RunManagerDeps;
}

export interface RunManagerDeps {
  resolveProviderBundle?: (settings?: AppSettings) => RoleProviderBundle;
}

export interface EnvironmentInfo {
  scenarios: Array<{
    name: string;
    description: string;
    origin?: "builtin" | "custom" | "unknown";
    available?: boolean;
  }>;
  executors: Array<{ mode: string; available: boolean; description: string }>;
  currentExecutor: string;
  agentProvider: string;
  routingContext?: {
    provider: string;
    model?: string;
    hostingClass?: string;
    capabilityTier?: string;
    roles: Record<string, { provider: string; model: string; capabilityTier?: string }>;
  };
}

export interface RunManagerState {
  active: boolean;
  paused: boolean;
  runId: string | null;
  scenario: string | null;
  generation: number | null;
  phase: string | null;
}

export type { ScenarioPreviewInfo } from "../scenarios/draft-workflow.js";

export type ScenarioReadyInfo = InteractiveScenarioReadyInfo;

export class RunManager {
  readonly #opts: RunManagerOpts;
  #active = false;
  readonly #controller = new LoopController();
  readonly #events: EventStreamEmitter;
  readonly #stateSubscribers: Array<(state: RunManagerState) => void> = [];
  #completedGenerations = 0;
  #bestScore: number | null = null;
  #state: RunManagerState = {
    active: false,
    paused: false,
    runId: null,
    scenario: null,
    generation: null,
    phase: null,
  };
  readonly #customScenarioRegistry: RunCustomScenarioRegistry;
  readonly #providerSession: RunManagerProviderSession;
  readonly #scenarioSession: InteractiveScenarioSession;
  #providerChangeLeaseActive = false;
  #activeHintSupportsImageAttachments = false;

  constructor(opts: RunManagerOpts) {
    this.#opts = opts;
    this.#events = new EventStreamEmitter(join(opts.runsRoot, "_interactive", "events.ndjson"));
    this.#customScenarioRegistry = new RunCustomScenarioRegistry({
      knowledgeRoot: opts.knowledgeRoot,
    });
    this.#providerSession = new RunManagerProviderSession({
      providerType: opts.providerType,
      apiKey: opts.apiKey,
      baseUrl: opts.baseUrl,
      model: opts.model,
    });
    this.#scenarioSession = new InteractiveScenarioSession({
      knowledgeRoot: opts.knowledgeRoot,
      humanizeName: (name) => this.#humanizeName(name),
    });
    this.#events.subscribe((event, payload) => {
      this.#applyEventProgress(event, payload);
      this.#applyEventState(event, payload);
    });
    this.#reloadCustomScenarios();
  }

  get isActive(): boolean {
    return this.#active;
  }

  getDbPath(): string {
    return this.#opts.dbPath;
  }

  getMigrationsDir(): string {
    return this.#opts.migrationsDir;
  }

  getRunsRoot(): string {
    return this.#opts.runsRoot;
  }

  getKnowledgeRoot(): string {
    return this.#opts.knowledgeRoot;
  }

  getSkillsRoot(): string {
    return this.#opts.skillsRoot ?? join(dirname(this.#opts.knowledgeRoot), "skills");
  }

  buildMissionProvider() {
    return this.buildProvider();
  }

  listScenarios(): string[] {
    return Object.keys(SCENARIO_REGISTRY).sort();
  }

  getEnvironmentInfo(): EnvironmentInfo {
    return buildEnvironmentInfo({
      builtinScenarioNames: this.listScenarios(),
      getBuiltinScenarioClass: (name) => SCENARIO_REGISTRY[name],
      customScenarios: this.#customScenarioRegistry.asMap(),
      activeProviderType: this.getActiveProviderType(),
      routingContext: this.#providerSession.describeRoutingContext(),
    });
  }

  getActiveProviderType(): string | null {
    return this.#providerSession.getActiveProviderType();
  }

  setActiveProvider(config: {
    providerType: string;
    apiKey?: string;
    baseUrl?: string;
    model?: string;
  }): void {
    this.#assertProviderCanChange();
    this.#providerSession.setActiveProvider(config);
  }

  clearActiveProvider(): void {
    this.#assertProviderCanChange();
    this.#providerSession.clearActiveProvider();
  }

  /**
   * Reserve provider configuration while an auth workflow validates and
   * persists credentials. Starting a run is rejected until the lease is
   * released, so persistence cannot succeed only to have the in-memory switch
   * fail because another client started a run in the meantime.
   */
  acquireProviderChangeLease(): () => void {
    this.#assertProviderCanChange();
    if (this.#providerChangeLeaseActive) {
      throw new Error("Another provider configuration change is already in progress");
    }
    this.#providerChangeLeaseActive = true;
    let released = false;
    return () => {
      if (released) return;
      released = true;
      this.#providerChangeLeaseActive = false;
    };
  }

  getState(): RunManagerState {
    return { ...this.#state };
  }

  get events(): EventStreamEmitter {
    return this.#events;
  }

  subscribeEvents(callback: EventCallback): void {
    this.#events.subscribe(callback);
  }

  unsubscribeEvents(callback: EventCallback): void {
    this.#events.unsubscribe(callback);
  }

  subscribeState(callback: (state: RunManagerState) => void): void {
    this.#stateSubscribers.push(callback);
  }

  unsubscribeState(callback: (state: RunManagerState) => void): void {
    const idx = this.#stateSubscribers.indexOf(callback);
    if (idx !== -1) {
      this.#stateSubscribers.splice(idx, 1);
    }
  }

  pause(expectedRunId?: string | null): void {
    if (expectedRunId !== undefined) this.#assertActiveRunControl("pause", expectedRunId);
    this.#controller.pause();
    this.#updateState({ paused: true });
  }

  resume(expectedRunId?: string | null): void {
    if (expectedRunId !== undefined) this.#assertActiveRunControl("resume", expectedRunId);
    this.#controller.resume();
    this.#updateState({ paused: false });
  }

  stop(runId: string, commandId: string): "already_requested" | "already_terminal" | "requested" {
    const state = this.getState();
    if (state.runId !== runId) {
      throw new Error("run_id does not match the current engine run");
    }
    if (!this.#active || isTerminalRunPhase(state.phase)) {
      return "already_terminal";
    }
    const decision = this.#controller.requestStop(runId, commandId);
    if (decision === "requested") {
      this.#updateState({ paused: false, phase: "stopping" });
    }
    return decision;
  }

  injectHint(
    text: string,
    imageAttachments: readonly ValidatedImageAttachment[] = [],
    expectedRunId?: string | null,
  ): void {
    if (!this.#active || isTerminalRunPhase(this.#state.phase)) {
      throw new Error("Cannot inject a hint when no run is active");
    }
    if (expectedRunId !== undefined && this.#state.runId !== expectedRunId) {
      throw new Error("run_id does not match the current engine run");
    }
    if (
      imageAttachments.length > 0 &&
      !this.#activeHintSupportsImageAttachments
    ) {
      throw new Error("The active competitor provider/model does not support image attachments");
    }
    this.#controller.injectHint(text, imageAttachments);
  }

  overrideGate(
    decision: "advance" | "retry" | "rollback",
    expectedRunId?: string | null,
  ): void {
    if (!this.#active || isTerminalRunPhase(this.#state.phase)) {
      throw new Error("Cannot override a gate when no run is active");
    }
    if (expectedRunId !== undefined && this.#state.runId !== expectedRunId) {
      throw new Error("run_id does not match the current engine run");
    }
    this.#controller.setGateOverride(decision);
  }

  async chatAgent(
    role: string,
    message: string,
    imageAttachments: readonly ValidatedImageAttachment[] = [],
    expectedRunId?: string | null,
  ): Promise<string> {
    if (expectedRunId !== undefined && this.#state.runId !== expectedRunId) {
      throw new Error("run_id does not match the current engine run");
    }
    return executeChatAgentInteraction({
      role,
      message,
      state: this.getState(),
      resolveProviderBundle: () => this.#resolveProviderBundle(),
      imageAttachments,
    });
  }

  getInteractiveCapabilities(): string[] {
    try {
      return this.#providerSession.supportsInteractiveImageAttachments()
        ? [IMAGE_ATTACHMENTS_CAPABILITY]
        : [];
    } catch {
      return [];
    }
  }

  async startRun(
    scenario: string,
    generations: number,
    optsOrRunId: string | { requirePlaybookApproval?: boolean } = {},
    maybeRunId?: string,
  ): Promise<string> {
    const requirePlaybookApproval =
      typeof optsOrRunId === "string" ? false : optsOrRunId.requirePlaybookApproval ?? false;
    const runId = typeof optsOrRunId === "string" ? optsOrRunId : maybeRunId;
    if (this.#active) {
      throw new Error("A run is already active");
    }
    if (this.#providerChangeLeaseActive) {
      throw new Error("Cannot start a run while a provider configuration change is in progress");
    }

    const customScenario = this.#customScenarioRegistry.get(scenario);
    const family = customScenario ? readScenarioFamily(customScenario.path) : null;
    const plan = resolveRunStartPlan({
      scenario,
      builtinScenarioNames: Object.keys(SCENARIO_REGISTRY),
      customScenario,
      customScenarioFamily: family,
    });

    const id = runId ?? `tui_${Date.now().toString(16).slice(-8)}`;

    if (plan.kind === "builtin_game") {
      const settings = loadSettings();
      const providerBundle = this.#resolveProviderBundle(
        settings,
        this.#runtimeSessionOptsForRun(id, plan.scenarioName),
      );
      const scenarioInstance = resolveBuiltInGameScenario({
        scenarioName: plan.scenarioName,
      });
      const competitorProvider =
        providerBundle.roleProviders.competitor ?? providerBundle.defaultProvider;
      const competitorModel =
        providerBundle.roleModels.competitor ?? providerBundle.defaultConfig.model;
      this.#activateRun(
        id,
        scenario,
        providerSupportsImageAttachments(competitorProvider, competitorModel),
      );
      this.#startManagedExecution(
        id,
        () => executeBuiltInGameStartRun({
          runId: id,
          scenarioName: plan.scenarioName,
          generations,
          requirePlaybookApproval,
          settings,
          providerBundle,
          opts: this.#opts,
          controller: this.#controller,
          events: this.#events,
          scenario: scenarioInstance,
        }),
      );
      return id;
    }

    if (plan.kind === "agent_task_custom") {
      const settings = loadSettings();
      const providerBundle = this.#resolveProviderBundle(
        settings,
        this.#runtimeSessionOptsForRun(id, plan.scenarioName),
      );
      this.#activateRun(
        id,
        scenario,
        providerSupportsImageAttachments(
          providerBundle.defaultProvider,
          providerBundle.defaultConfig.model,
        ),
      );
      this.#startManagedExecution(
        id,
        async () => {
          try {
            await executeAgentTaskCustomStartRun({
              runId: id,
              scenarioName: plan.scenarioName,
              entry: plan.entry,
              generations,
              provider: providerBundle.defaultProvider,
              settings,
              persistence: {
                dbPath: this.#opts.dbPath,
                migrationsDir: this.#opts.migrationsDir,
                agentProvider: providerBundle.defaultConfig.providerType,
              },
              controller: this.#controller,
              events: this.#events,
            });
          } finally {
            providerBundle.close?.();
          }
        },
      );
      return id;
    }

    this.#activateRun(id, scenario, false);
    this.#startManagedExecution(
      id,
      () => executeGeneratedCustomStartRun({
        runId: id,
        scenarioName: plan.scenarioName,
        entry: plan.entry,
        family: plan.family,
        generations,
        knowledgeRoot: this.#opts.knowledgeRoot,
        controller: this.#controller,
        events: this.#events,
      }),
    );

    return id;
  }

  async createScenario(
    description: string,
    setupScope?: InteractiveScenarioScope,
  ): Promise<ScenarioPreviewInfo> {
    const providerBundle = this.#resolveProviderBundle();
    try {
      return await this.#scenarioSession.createScenario({
        description,
        provider: providerBundle.defaultProvider,
        scope: setupScope,
      });
    } finally {
      providerBundle.close?.();
    }
  }

  async createTask(
    contract: ImprovementTaskContract,
    sourceContents: TaskDataSourceContent[],
    setupScope?: InteractiveScenarioScope,
  ): Promise<ScenarioPreviewInfo> {
    return this.#scenarioSession.createTask({
      contract,
      sourceContents,
      scope: setupScope,
    });
  }

  async reviseScenario(
    feedback: string,
    setupScope?: InteractiveScenarioScope,
  ): Promise<ScenarioPreviewInfo> {
    const providerBundle = this.#resolveProviderBundle();
    try {
      return await this.#scenarioSession.reviseScenario({
        feedback,
        provider: providerBundle.defaultProvider,
        scope: setupScope,
      });
    } finally {
      providerBundle.close?.();
    }
  }

  cancelScenario(setupScope?: InteractiveScenarioScope): void {
    this.#scenarioSession.cancelScenario(setupScope);
  }

  async confirmScenario(setupScope?: InteractiveScenarioScope): Promise<ScenarioReadyInfo> {
    const ready = await this.#scenarioSession.confirmScenario(setupScope);
    this.#reloadCustomScenarios();
    return ready;
  }

  #resolveProviderBundle(
    settings = loadSettings(),
    runtimeSession?: ProviderRuntimeSessionOpts,
  ) {
    if (this.#opts.deps?.resolveProviderBundle) {
      return this.#opts.deps.resolveProviderBundle(settings);
    }
    return this.#providerSession.resolveProviderBundle(
      settings,
      runtimeSession ? { runtimeSession } : undefined,
    );
  }

  #runtimeSessionOptsForRun(runId: string, scenarioName: string): ProviderRuntimeSessionOpts {
    return {
      sessionId: runtimeSessionIdForRun(runId),
      goal: `autoctx run ${scenarioName}`,
      dbPath: this.#opts.dbPath,
      workspaceRoot: process.cwd(),
      metadata: {
        command: "serve",
        runId,
        scenarioName,
      },
      eventSink: createRuntimeSessionEventStreamSink(this.#events),
    };
  }

  buildProvider(role?: GenerationRole) {
    return this.#providerSession.buildProvider(role, loadSettings());
  }

  #startManagedExecution(runId: string, execute: () => Promise<void>): void {
    void createManagedRunExecution({
      runId,
      // Let the WebSocket start command record and send run_accepted before
      // execution can synchronously emit run/task-plan frames.
      execute: async () => {
        await new Promise<void>((resolve) => setImmediate(resolve));
        await execute();
      },
      events: this.#events,
      getPaused: () => this.#controller.isPaused(),
      getRunPhase: () => this.#state.phase,
      getStopProgress: () => ({
        completedGenerations: this.#completedGenerations,
        ...(this.#bestScore === null ? {} : { bestScore: this.#bestScore }),
      }),
      getStopRequest: () => this.#controller.getStopRequest(),
      setActive: (active) => {
        this.#active = active;
        if (!active) {
          this.#activeHintSupportsImageAttachments = false;
          this.#controller.endRun(runId);
        }
      },
      updateState: (patch) => {
        this.#updateState(patch);
      },
    });
  }

  #activateRun(
    runId: string,
    scenario: string,
    hintSupportsImageAttachments: boolean,
  ): void {
    this.#controller.beginRun(runId);
    this.#activeHintSupportsImageAttachments = hintSupportsImageAttachments;
    this.#completedGenerations = 0;
    this.#bestScore = null;
    this.#active = true;
    this.#updateState(buildQueuedRunStatePatch({
      runId,
      scenario,
      paused: this.#controller.isPaused(),
    }));
  }

  #assertActiveRunControl(action: "pause" | "resume", expectedRunId?: string | null): void {
    if (!this.#active || isTerminalRunPhase(this.#state.phase)) {
      throw new Error(`Cannot ${action} when no run is active`);
    }
    if (expectedRunId !== undefined && this.#state.runId !== expectedRunId) {
      throw new Error("run_id does not match the current engine run");
    }
  }

  #applyEventProgress(event: string, payload: Record<string, unknown>): void {
    if (event !== "generation_completed") return;
    const generation = payload.generation;
    if (typeof generation === "number" && Number.isInteger(generation)) {
      this.#completedGenerations = Math.max(this.#completedGenerations, generation);
    }
    const bestScore = payload.best_score;
    if (typeof bestScore === "number" && Number.isFinite(bestScore)) {
      this.#bestScore = this.#bestScore === null
        ? bestScore
        : Math.max(this.#bestScore, bestScore);
    }
  }

  #applyEventState(event: string, payload: Record<string, unknown>): void {
    const patch = buildRunEventStatePatch(event, payload, this.#state);
    if (patch) {
      this.#updateState(patch);
    }
  }

  #updateState(patch: Partial<RunManagerState>): void {
    this.#state = mergeRunManagerState(this.#state, patch);
    notifyRunStateSubscribers(this.#stateSubscribers, this.getState());
  }

  #reloadCustomScenarios(): void {
    this.#customScenarioRegistry.reload();
  }

  #humanizeName(name: string): string {
    return name
      .split(/[_-]+/)
      .filter(Boolean)
      .map((part) => part[0]!.toUpperCase() + part.slice(1))
      .join(" ");
  }

  #assertProviderCanChange(): void {
    if (this.#active) {
      throw new Error("Cannot change providers while a run is active");
    }
  }
}
