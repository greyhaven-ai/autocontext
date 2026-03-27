/**
 * Run manager — manages run lifecycle for interactive server (AC-347 Task 26).
 * Mirrors Python's autocontext/server/run_manager.py.
 */

import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { GenerationRunner } from "../loop/generation-runner.js";
import { LoopController } from "../loop/controller.js";
import { EventStreamEmitter } from "../loop/events.js";
import type { EventCallback } from "../loop/events.js";
import {
  buildRoleProviderBundle,
  type GenerationRole,
} from "../providers/index.js";
import {
  assertFamilyContract,
  type CreatedScenarioResult,
  type CustomScenarioEntry,
  type IntentValidationResult,
  createScenarioFromDescription,
  IntentValidator,
  loadCustomScenarios,
  registerCustomScenarios,
  reviseSpec,
} from "../scenarios/index.js";
import { getScenarioTypeMarker, type ScenarioFamilyName } from "../scenarios/families.js";
import { executeGeneratedScenarioEntry } from "../scenarios/codegen/executor.js";
import { readScenarioFamily } from "../scenarios/codegen/loader.js";
import { SCENARIO_REGISTRY } from "../scenarios/registry.js";
import { SQLiteStore } from "../storage/index.js";
import { loadSettings } from "../config/index.js";

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

export interface ScenarioPreviewInfo {
  name: string;
  displayName: string;
  description: string;
  strategyParams: Array<{ name: string; description: string }>;
  scoringComponents: Array<{ name: string; description: string; weight: number }>;
  constraints: string[];
  winThreshold: number;
}

export interface ScenarioReadyInfo {
  name: string;
  testScores: number[];
}

interface PendingScenarioDraft {
  description: string;
  detectedFamily: string;
  preview: CreatedScenarioResult;
  validation: IntentValidationResult;
}

function readStringValue(spec: Record<string, unknown>, ...keys: string[]): string | undefined {
  for (const key of keys) {
    const value = spec[key];
    if (typeof value === "string" && value.trim().length > 0) {
      return value;
    }
  }
  return undefined;
}

export class RunManager {
  private readonly opts: RunManagerOpts;
  private providerOverride:
    | { providerType: string; apiKey?: string; baseUrl?: string; model?: string }
    | null
    | undefined;
  private _active = false;
  private _runPromise: Promise<void> | null = null;
  private readonly controller = new LoopController();
  private readonly events: EventStreamEmitter;
  private readonly stateSubscribers: Array<(state: RunManagerState) => void> = [];
  private state: RunManagerState = {
    active: false,
    paused: false,
    runId: null,
    scenario: null,
    generation: null,
    phase: null,
  };
  private customScenarios = new Map<string, CustomScenarioEntry>();
  private pendingScenario: PendingScenarioDraft | null = null;

  constructor(opts: RunManagerOpts) {
    this.opts = opts;
    this.events = new EventStreamEmitter(join(opts.runsRoot, "_interactive", "events.ndjson"));
    this.events.subscribe((event, payload) => {
      this.applyEventState(event, payload);
    });
    this.reloadCustomScenarios();
  }

  get isActive(): boolean {
    return this._active;
  }

  listScenarios(): string[] {
    return Object.keys(SCENARIO_REGISTRY).sort();
  }

  getEnvironmentInfo(): EnvironmentInfo {
    const builtinScenarios = this.listScenarios().map((name) => {
      const ScenarioClass = SCENARIO_REGISTRY[name];
      const instance = new ScenarioClass();
      assertFamilyContract(instance, "game", `scenario '${name}'`);
      return { name, description: instance.describeRules() };
    });
    const customScenarios = [...this.customScenarios.values()]
      .filter((entry) => !(entry.name in SCENARIO_REGISTRY))
      .sort((a, b) => a.name.localeCompare(b.name))
      .map((entry) => ({
        name: entry.name,
        description: this.describeCustomScenario(entry),
      }));

    return {
      scenarios: [...builtinScenarios, ...customScenarios],
      executors: [
        { mode: "local", available: true, description: "Local subprocess execution" },
      ],
      currentExecutor: "local",
      agentProvider: this.getActiveProviderType() ?? "none",
    };
  }

  getActiveProviderType(): string | null {
    if (this.providerOverride === null) {
      return null;
    }
    return this.providerOverride?.providerType ?? this.opts.providerType ?? loadSettings().agentProvider;
  }

  setActiveProvider(config: {
    providerType: string;
    apiKey?: string;
    baseUrl?: string;
    model?: string;
  }): void {
    this.providerOverride = {
      providerType: config.providerType.trim().toLowerCase(),
      ...(config.apiKey ? { apiKey: config.apiKey } : {}),
      ...(config.baseUrl ? { baseUrl: config.baseUrl } : {}),
      ...(config.model ? { model: config.model } : {}),
    };
  }

  clearActiveProvider(): void {
    this.providerOverride = null;
  }

  getState(): RunManagerState {
    return { ...this.state };
  }

  subscribeEvents(callback: EventCallback): void {
    this.events.subscribe(callback);
  }

  unsubscribeEvents(callback: EventCallback): void {
    this.events.unsubscribe(callback);
  }

  subscribeState(callback: (state: RunManagerState) => void): void {
    this.stateSubscribers.push(callback);
  }

  unsubscribeState(callback: (state: RunManagerState) => void): void {
    const idx = this.stateSubscribers.indexOf(callback);
    if (idx !== -1) {
      this.stateSubscribers.splice(idx, 1);
    }
  }

  pause(): void {
    this.controller.pause();
    this.updateState({ paused: true });
  }

  resume(): void {
    this.controller.resume();
    this.updateState({ paused: false });
  }

  injectHint(text: string): void {
    this.controller.injectHint(text);
  }

  overrideGate(decision: "advance" | "retry" | "rollback"): void {
    this.controller.setGateOverride(decision);
  }

  async chatAgent(role: string, message: string): Promise<string> {
    const normalizedRole = isGenerationRole(role) ? role : undefined;
    const bundle = this.resolveProviderBundle();
    const provider = this.buildProvider(normalizedRole);
    const state = this.getState();
    const response = await provider.complete({
      systemPrompt: "",
      model: normalizedRole ? bundle.roleModels[normalizedRole] : bundle.defaultConfig.model,
      userPrompt: [
        `[${role}]`,
        "You are helping from the interactive autocontext control plane.",
        `Run active: ${state.active ? "yes" : "no"}`,
        `Scenario: ${state.scenario ?? "none"}`,
        `Generation: ${state.generation ?? 0}`,
        `Phase: ${state.phase ?? "idle"}`,
        `Operator message: ${message}`,
      ].join("\n"),
    });
    return response.text;
  }

  async startRun(scenario: string, generations: number, runId?: string): Promise<string> {
    if (this._active) {
      throw new Error("A run is already active");
    }

    const ScenarioClass = SCENARIO_REGISTRY[scenario];
    const customScenario = this.customScenarios.get(scenario);
    if (!ScenarioClass && !customScenario) {
      throw new Error(`Unknown scenario: ${scenario}. Available: ${this.listScenarios().join(", ")}`);
    }

    const id = runId ?? `tui_${Date.now().toString(16).slice(-8)}`;
    this._active = true;
    this.updateState({
      active: true,
      paused: this.controller.isPaused(),
      runId: id,
      scenario,
      generation: null,
      phase: "queued",
    });

    if (ScenarioClass) {
      const settings = loadSettings();
      const providerBundle = this.resolveProviderBundle(settings);
      const scenarioInstance = new ScenarioClass();
      assertFamilyContract(scenarioInstance, "game", `scenario '${scenario}'`);

      const store = new SQLiteStore(this.opts.dbPath);
      store.migrate(this.opts.migrationsDir);

      const runner = new GenerationRunner({
        provider: providerBundle.defaultProvider,
        roleProviders: providerBundle.roleProviders,
        roleModels: providerBundle.roleModels,
        scenario: scenarioInstance,
        store,
        runsRoot: this.opts.runsRoot,
        knowledgeRoot: this.opts.knowledgeRoot,
        matchesPerGeneration: settings.matchesPerGeneration,
        maxRetries: settings.maxRetries,
        minDelta: settings.backpressureMinDelta,
        playbookMaxVersions: settings.playbookMaxVersions,
        contextBudgetTokens: settings.contextBudgetTokens,
        curatorEnabled: settings.curatorEnabled,
        curatorConsolidateEveryNGens: settings.curatorConsolidateEveryNGens,
        skillMaxLessons: settings.skillMaxLessons,
        deadEndTrackingEnabled: settings.deadEndTrackingEnabled,
        deadEndMaxEntries: settings.deadEndMaxEntries,
        stagnationResetEnabled: settings.stagnationResetEnabled,
        stagnationRollbackThreshold: settings.stagnationRollbackThreshold,
        stagnationPlateauWindow: settings.stagnationPlateauWindow,
        stagnationPlateauEpsilon: settings.stagnationPlateauEpsilon,
        stagnationDistillTopLessons: settings.stagnationDistillTopLessons,
        explorationMode: settings.explorationMode,
        notifyWebhookUrl: settings.notifyWebhookUrl,
        notifyOn: settings.notifyOn,
        controller: this.controller,
        events: this.events,
      });

      this._runPromise = runner
        .run(id, generations)
        .then(() => {})
        .catch((err) => {
          this.events.emit("run_failed", {
            run_id: id,
            error: err instanceof Error ? err.message : String(err),
          });
        })
        .finally(() => {
          this._active = false;
          this.updateState({
            active: false,
            paused: this.controller.isPaused(),
            generation: null,
            phase: null,
          });
          store.close();
        });
      return id;
    }

    const family = customScenario ? readScenarioFamily(customScenario.path) : null;
    if (
      !customScenario
      || !customScenario.hasGeneratedSource
      || !family
      || family === "agent_task"
    ) {
      throw new Error(
        `Scenario '${scenario}' is a saved custom ${customScenario?.type ?? "unknown"} scenario. ` +
        "It is discoverable in the TS control plane, but /run currently supports only built-in game scenarios and generated non-agent-task scenarios.",
      );
    }

    this._runPromise = this.runGeneratedCustomScenario(id, scenario, customScenario, family, generations)
      .catch((err) => {
        this.events.emit("run_failed", {
          run_id: id,
          error: err instanceof Error ? err.message : String(err),
        });
      })
      .finally(() => {
        this._active = false;
        this.updateState({
          active: false,
          paused: this.controller.isPaused(),
          generation: null,
          phase: null,
        });
      });

    return id;
  }

  async createScenario(description: string): Promise<ScenarioPreviewInfo> {
    const provider = this.buildProvider();
    const created = await createScenarioFromDescription(description, provider);
    const preview: CreatedScenarioResult = created.family === "agent_task"
      ? created
      : { ...created, family: "agent_task" };
    const validation = new IntentValidator().validate(description, {
      name: preview.name,
      taskPrompt: preview.spec.taskPrompt,
      rubric: preview.spec.rubric,
      description: preview.spec.description,
    });
    const draft: PendingScenarioDraft = {
      description,
      detectedFamily: created.family,
      preview,
      validation,
    };
    this.pendingScenario = draft;
    return this.buildScenarioPreview(draft);
  }

  async reviseScenario(feedback: string): Promise<ScenarioPreviewInfo> {
    if (!this.pendingScenario) {
      throw new Error("No scenario preview is pending. Create a scenario first.");
    }
    const provider = this.buildProvider();
    const revision = await reviseSpec({
      currentSpec: this.pendingScenario.preview.spec,
      feedback,
      family: this.pendingScenario.preview.family,
      provider,
    });
    if (!revision.changesApplied) {
      throw new Error(revision.error ?? "Scenario revision failed.");
    }

    const revisedPreview: CreatedScenarioResult = {
      ...this.pendingScenario.preview,
      spec: {
        ...revision.revised,
        taskPrompt: readStringValue(revision.revised, "taskPrompt", "task_prompt")
          ?? this.pendingScenario.preview.spec.taskPrompt,
        rubric: readStringValue(revision.revised, "rubric", "judgeRubric", "judge_rubric")
          ?? this.pendingScenario.preview.spec.rubric,
        description: readStringValue(revision.revised, "description")
          ?? this.pendingScenario.preview.spec.description,
      },
    };
    const validation = new IntentValidator().validate(this.pendingScenario.description, {
      name: revisedPreview.name,
      taskPrompt: revisedPreview.spec.taskPrompt,
      rubric: revisedPreview.spec.rubric,
      description: revisedPreview.spec.description,
    });
    const draft: PendingScenarioDraft = {
      ...this.pendingScenario,
      preview: revisedPreview,
      validation,
    };
    this.pendingScenario = draft;
    return this.buildScenarioPreview(draft);
  }

  cancelScenario(): void {
    this.pendingScenario = null;
  }

  async confirmScenario(): Promise<ScenarioReadyInfo> {
    if (!this.pendingScenario) {
      throw new Error("No scenario preview is pending. Create a scenario first.");
    }
    if (!this.pendingScenario.validation.valid) {
      throw new Error(this.pendingScenario.validation.issues.join("; "));
    }
    const pending = this.pendingScenario;
    this.persistPendingScenario(pending);
    this.pendingScenario = null;
    this.reloadCustomScenarios();
    return { name: pending.preview.name, testScores: [] };
  }

  private resolveProviderBundle(settings = loadSettings()) {
    if (this.providerOverride === null) {
      throw new Error("No active provider configured for this session. Use /login or /provider.");
    }

    const overrides = this.providerOverride ?? {
      providerType: this.opts.providerType,
      apiKey: this.opts.apiKey,
      baseUrl: this.opts.baseUrl,
      model: this.opts.model,
    };

    return buildRoleProviderBundle(settings, {
      providerType: overrides.providerType,
      apiKey: overrides.apiKey,
      baseUrl: overrides.baseUrl,
      model: overrides.model,
    });
  }

  private buildProvider(role?: GenerationRole) {
    const bundle = this.resolveProviderBundle();
    if (role) {
      return bundle.roleProviders[role] ?? bundle.defaultProvider;
    }
    return bundle.defaultProvider;
  }

  private applyEventState(event: string, payload: Record<string, unknown>): void {
    switch (event) {
      case "run_started":
        this.updateState({
          runId: (payload.run_id as string) ?? this.state.runId,
          scenario: (payload.scenario as string) ?? this.state.scenario,
          phase: "run",
        });
        return;
      case "generation_started":
        this.updateState({
          generation: (payload.generation as number) ?? this.state.generation,
          phase: "agents",
        });
        return;
      case "agents_started":
        this.updateState({ phase: "agents" });
        return;
      case "tournament_started":
        this.updateState({ phase: "tournament" });
        return;
      case "gate_decided":
        this.updateState({ phase: "gate" });
        return;
      case "generation_completed":
        this.updateState({
          generation: (payload.generation as number) ?? this.state.generation,
          phase: "support",
        });
        return;
      case "run_completed":
        this.updateState({ phase: "completed" });
        return;
      case "run_failed":
        this.updateState({ phase: "failed" });
        return;
      default:
        return;
    }
  }

  private updateState(patch: Partial<RunManagerState>): void {
    this.state = { ...this.state, ...patch };
    const snapshot = this.getState();
    for (const subscriber of [...this.stateSubscribers]) {
      try {
        subscriber(snapshot);
      } catch {
        // State observers should never crash the active run.
      }
    }
  }

  private reloadCustomScenarios(): void {
    const customDir = join(this.opts.knowledgeRoot, "_custom_scenarios");
    const loaded = loadCustomScenarios(customDir);
    registerCustomScenarios(loaded);
    this.customScenarios = loaded;
  }

  private describeCustomScenario(entry: CustomScenarioEntry): string {
    if (entry.type === "agent_task") {
      const taskPrompt = typeof entry.spec.taskPrompt === "string"
        ? entry.spec.taskPrompt
        : entry.name;
      return `Custom agent task: ${taskPrompt} (saved for custom-scenario tooling; not runnable via /run yet)`;
    }
    const description = typeof entry.spec.description === "string"
      ? entry.spec.description
      : `Custom ${entry.type} scenario`;
    if (entry.hasGeneratedSource) {
      return `${description} (generated custom scenario; runnable via /run)`;
    }
    return `${description} (saved custom scenario; not runnable via /run yet)`;
  }

  private async runGeneratedCustomScenario(
    runId: string,
    scenarioName: string,
    entry: CustomScenarioEntry,
    family: ScenarioFamilyName,
    generations: number,
  ): Promise<void> {
    const customDir = join(this.opts.knowledgeRoot, "_custom_scenarios");
    const maxSteps = typeof entry.spec.max_steps === "number"
      ? entry.spec.max_steps
      : typeof entry.spec.maxSteps === "number"
        ? entry.spec.maxSteps
        : undefined;

    this.events.emit("run_started", {
      run_id: runId,
      scenario: scenarioName,
      target_generations: generations,
      family,
      generated_custom: true,
    });

    let bestScoreOverall = 0;
    for (let generation = 1; generation <= generations; generation++) {
      await this.controller.waitIfPaused();
      this.events.emit("generation_started", { run_id: runId, generation });

      const result = await executeGeneratedScenarioEntry({
        customDir,
        name: scenarioName,
        family,
        seed: generation,
        ...(typeof maxSteps === "number" ? { maxSteps } : {}),
      });

      bestScoreOverall = Math.max(bestScoreOverall, result.score);
      this.events.emit("generation_completed", {
        run_id: runId,
        generation,
        mean_score: result.score,
        best_score: result.score,
        elo: 1000,
        gate_decision: "advance",
        family,
        steps_executed: result.stepsExecuted,
        reasoning: result.reasoning,
      });
    }

    this.events.emit("run_completed", {
      run_id: runId,
      completed_generations: generations,
      best_score: bestScoreOverall,
      elo: 1000,
      family,
      generated_custom: true,
    });
  }

  private buildScenarioPreview(draft: PendingScenarioDraft): ScenarioPreviewInfo {
    const constraints = draft.validation.valid
      ? [`Intent validated at ${(draft.validation.confidence * 100).toFixed(0)}% confidence.`]
      : draft.validation.issues;
    if (draft.detectedFamily !== draft.preview.family) {
      constraints.push(
        `Detected ${draft.detectedFamily} signals, but the interactive TS creator currently saves agent-task scaffolds only.`,
      );
    }
    return {
      name: draft.preview.name,
      displayName: this.humanizeName(draft.preview.name),
      description: `${draft.preview.spec.description} [family: ${draft.preview.family}]`,
      strategyParams: [
        { name: "family", description: draft.preview.family },
        { name: "task_prompt", description: draft.preview.spec.taskPrompt },
      ],
      scoringComponents: [
        { name: "rubric", description: draft.preview.spec.rubric, weight: 1.0 },
      ],
      constraints,
      winThreshold: Number(draft.validation.confidence.toFixed(3)),
    };
  }

  private persistPendingScenario(draft: PendingScenarioDraft): void {
    const scenarioDir = join(this.opts.knowledgeRoot, "_custom_scenarios", draft.preview.name);
    if (!existsSync(scenarioDir)) {
      mkdirSync(scenarioDir, { recursive: true });
    }

    const family = draft.preview.family as ScenarioFamilyName;
    const scenarioType = getScenarioTypeMarker(family);
    writeFileSync(join(scenarioDir, "scenario_type.txt"), scenarioType, "utf-8");
    writeFileSync(
      join(scenarioDir, "spec.json"),
      JSON.stringify({
        name: draft.preview.name,
        scenario_type: scenarioType,
        description: draft.preview.spec.description,
        taskPrompt: draft.preview.spec.taskPrompt,
        rubric: draft.preview.spec.rubric,
        intent_confidence: draft.validation.confidence,
        intent_issues: draft.validation.issues,
      }, null, 2),
      "utf-8",
    );

    if (family === "agent_task") {
      writeFileSync(
        join(scenarioDir, "agent_task_spec.json"),
        JSON.stringify({
          task_prompt: draft.preview.spec.taskPrompt,
          judge_rubric: draft.preview.spec.rubric,
          output_format: "free_text",
          max_rounds: 1,
          quality_threshold: 0.9,
        }, null, 2),
        "utf-8",
      );
    }
  }

  private humanizeName(name: string): string {
    return name
      .split(/[_-]+/)
      .filter(Boolean)
      .map((part) => part[0]!.toUpperCase() + part.slice(1))
      .join(" ");
  }
}

function isGenerationRole(value: string): value is GenerationRole {
  return value === "competitor"
    || value === "analyst"
    || value === "coach"
    || value === "architect"
    || value === "curator";
}
