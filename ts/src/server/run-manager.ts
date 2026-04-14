/**
 * Run manager — manages run lifecycle for interactive server (AC-347 Task 26).
 * Mirrors Python's autocontext/server/run_manager.py.
 */

import { join } from "node:path";
import { LoopController } from "../loop/controller.js";
import { EventStreamEmitter } from "../loop/events.js";
import type { EventCallback } from "../loop/events.js";
import type { GenerationRole } from "../providers/index.js";
import type { ScenarioPreviewInfo } from "../scenarios/draft-workflow.js";
import {
  InteractiveScenarioSession,
  type InteractiveScenarioReadyInfo,
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
  mergeRunManagerState,
  notifyRunStateSubscribers,
} from "./run-state-workflow.js";
import { buildEnvironmentInfo } from "./run-environment-catalog.js";
import { executeChatAgentInteraction } from "./chat-agent-workflow.js";
import { RunCustomScenarioRegistry } from "./run-custom-scenario-registry.js";
import { RunManagerProviderSession } from "./run-manager-provider-session.js";
import {
  executeBuiltInGameStartRun,
  executeGeneratedCustomStartRun,
  resolveBuiltInGameScenario,
  resolveRunStartPlan,
} from "./run-start-workflow.js";

export interface RunManagerOpts {
  dbPath: string;
  migrationsDir: string;
  runsRoot: string;
  knowledgeRoot: string;
  providerType?: string;
  apiKey?: string;
  baseUrl?: string;
  model?: string;
}

export interface EnvironmentInfo {
  scenarios: Array<{ name: string; description: string }>;
  executors: Array<{ mode: string; available: boolean; description: string }>;
  currentExecutor: string;
  agentProvider: string;
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
  #runPromise: Promise<void> | null = null;
  readonly #controller = new LoopController();
  readonly #events: EventStreamEmitter;
  readonly #stateSubscribers: Array<(state: RunManagerState) => void> = [];
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
    this.#providerSession.setActiveProvider(config);
  }

  clearActiveProvider(): void {
    this.#providerSession.clearActiveProvider();
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

  pause(): void {
    this.#controller.pause();
    this.#updateState({ paused: true });
  }

  resume(): void {
    this.#controller.resume();
    this.#updateState({ paused: false });
  }

  injectHint(text: string): void {
    this.#controller.injectHint(text);
  }

  overrideGate(decision: "advance" | "retry" | "rollback"): void {
    this.#controller.setGateOverride(decision);
  }

  async chatAgent(role: string, message: string): Promise<string> {
    return executeChatAgentInteraction({
      role,
      message,
      state: this.getState(),
      resolveProviderBundle: () => this.#resolveProviderBundle(),
      buildProvider: (chatRole) => this.buildProvider(chatRole),
    });
  }

  async startRun(scenario: string, generations: number, runId?: string): Promise<string> {
    if (this.#active) {
      throw new Error("A run is already active");
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
    this.#active = true;
    this.#updateState(buildQueuedRunStatePatch({
      runId: id,
      scenario,
      paused: this.#controller.isPaused(),
    }));

    if (plan.kind === "builtin_game") {
      const settings = loadSettings();
      const providerBundle = this.#resolveProviderBundle(settings);
      const scenarioInstance = resolveBuiltInGameScenario({
        scenarioName: plan.scenarioName,
      });
      this.#runPromise = createManagedRunExecution({
        runId: id,
        execute: () => executeBuiltInGameStartRun({
          runId: id,
          scenarioName: plan.scenarioName,
          generations,
          settings,
          providerBundle,
          opts: this.#opts,
          controller: this.#controller,
          events: this.#events,
          scenario: scenarioInstance,
        }),
        events: this.#events,
        getPaused: () => this.#controller.isPaused(),
        setActive: (active) => {
          this.#active = active;
        },
        updateState: (patch) => {
          this.#updateState(patch);
        },
      });
      return id;
    }

    this.#runPromise = createManagedRunExecution({
      runId: id,
      execute: () => executeGeneratedCustomStartRun({
        runId: id,
        scenarioName: plan.scenarioName,
        entry: plan.entry,
        family: plan.family,
        generations,
        knowledgeRoot: this.#opts.knowledgeRoot,
        controller: this.#controller,
        events: this.#events,
      }),
      events: this.#events,
      getPaused: () => this.#controller.isPaused(),
      setActive: (active) => {
        this.#active = active;
      },
      updateState: (patch) => {
        this.#updateState(patch);
      },
    });

    return id;
  }

  async createScenario(description: string): Promise<ScenarioPreviewInfo> {
    return this.#scenarioSession.createScenario({
      description,
      provider: this.buildProvider(),
    });
  }

  async reviseScenario(feedback: string): Promise<ScenarioPreviewInfo> {
    return this.#scenarioSession.reviseScenario({
      feedback,
      provider: this.buildProvider(),
    });
  }

  cancelScenario(): void {
    this.#scenarioSession.cancelScenario();
  }

  async confirmScenario(): Promise<ScenarioReadyInfo> {
    const ready = await this.#scenarioSession.confirmScenario();
    this.#reloadCustomScenarios();
    return ready;
  }

  #resolveProviderBundle(settings = loadSettings()) {
    return this.#providerSession.resolveProviderBundle(settings);
  }

  buildProvider(role?: GenerationRole) {
    return this.#providerSession.buildProvider(role, loadSettings());
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
}

