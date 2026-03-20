/**
 * Run manager — manages run lifecycle for interactive server (AC-347 Task 26).
 * Mirrors Python's autocontext/server/run_manager.py.
 */

import { join } from "node:path";
import { SCENARIO_REGISTRY } from "../scenarios/registry.js";
import { SQLiteStore } from "../storage/index.js";
import { GenerationRunner } from "../loop/generation-runner.js";
import { createProvider } from "../providers/index.js";
import { EventStreamEmitter } from "../loop/events.js";
import type { EventCallback } from "../loop/events.js";
import { LoopController } from "../loop/controller.js";

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

export class RunManager {
  private opts: RunManagerOpts;
  private _active = false;
  private _runPromise: Promise<void> | null = null;
  private controller = new LoopController();
  private events: EventStreamEmitter;
  private stateSubscribers: Array<(state: RunManagerState) => void> = [];
  private state: RunManagerState = {
    active: false,
    paused: false,
    runId: null,
    scenario: null,
    generation: null,
    phase: null,
  };

  constructor(opts: RunManagerOpts) {
    this.opts = opts;
    this.events = new EventStreamEmitter(join(opts.runsRoot, "_interactive", "events.ndjson"));
    this.events.subscribe((event, payload) => {
      this.applyEventState(event, payload);
    });
  }

  get isActive(): boolean {
    return this._active;
  }

  listScenarios(): string[] {
    return Object.keys(SCENARIO_REGISTRY).sort();
  }

  getEnvironmentInfo(): EnvironmentInfo {
    const scenarios = this.listScenarios().map((name) => {
      const ScenarioClass = SCENARIO_REGISTRY[name];
      const instance = new ScenarioClass();
      return { name, description: instance.describeRules() };
    });

    return {
      scenarios,
      executors: [
        { mode: "local", available: true, description: "Local subprocess execution" },
      ],
      currentExecutor: "local",
      agentProvider: this.opts.providerType ?? "anthropic",
    };
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
    const provider = this.buildProvider();
    const state = this.getState();
    const response = await provider.complete({
      systemPrompt: "",
      userPrompt: [
        `[${role}]`,
        "You are helping from the interactive AutoContext control plane.",
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
    if (!ScenarioClass) {
      throw new Error(`Unknown scenario: ${scenario}. Available: ${this.listScenarios().join(", ")}`);
    }

    const id = runId ?? `tui_${Date.now().toString(16).slice(-8)}`;
    const provider = this.buildProvider();

    const store = new SQLiteStore(this.opts.dbPath);
    store.migrate(this.opts.migrationsDir);

    const runner = new GenerationRunner({
      provider,
      scenario: new ScenarioClass(),
      store,
      runsRoot: this.opts.runsRoot,
      knowledgeRoot: this.opts.knowledgeRoot,
      controller: this.controller,
      events: this.events,
    });

    this._active = true;
    this.updateState({
      active: true,
      paused: this.controller.isPaused(),
      runId: id,
      scenario,
      generation: null,
      phase: "queued",
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

  private buildProvider() {
    return createProvider({
      providerType: this.opts.providerType ?? "deterministic",
      apiKey: this.opts.apiKey,
      baseUrl: this.opts.baseUrl,
      model: this.opts.model,
    });
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
}
