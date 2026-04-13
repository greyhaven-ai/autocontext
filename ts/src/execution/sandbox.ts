/**
 * Sandbox manager — isolated scenario execution environments (AC-370).
 * Mirrors Python's autocontext/mcp/sandbox.py.
 */

import { ArtifactStore } from "../knowledge/artifact-store.js";
import { assertFamilyContract } from "../scenarios/family-interfaces.js";
import { SCENARIO_REGISTRY } from "../scenarios/registry.js";
import type { LLMProvider } from "../types/index.js";
import type { SQLiteStore } from "../storage/index.js";

export interface SandboxManagerOpts {
  provider: LLMProvider;
  store: SQLiteStore;
  runsRoot: string;
  knowledgeRoot: string;
  maxSandboxes?: number;
}

export interface Sandbox {
  sandboxId: string;
  scenarioName: string;
  userId: string;
  createdAt: string;
  status: "active" | "running" | "destroyed";
  runId?: string;
}

export class SandboxManager {
  #provider: LLMProvider;
  #store: SQLiteStore;
  #runsRoot: string;
  #knowledgeRoot: string;
  #maxSandboxes: number;
  #sandboxes = new Map<string, Sandbox>();

  constructor(opts: SandboxManagerOpts) {
    this.#provider = opts.provider;
    this.#store = opts.store;
    this.#runsRoot = opts.runsRoot;
    this.#knowledgeRoot = opts.knowledgeRoot;
    this.#maxSandboxes = opts.maxSandboxes ?? 10;
  }

  create(scenarioName: string, userId = "anonymous"): Sandbox {
    if (this.#sandboxes.size >= this.#maxSandboxes) {
      throw new Error(`Maximum sandbox limit (${this.#maxSandboxes}) reached`);
    }
    if (!(scenarioName in SCENARIO_REGISTRY)) {
      const supported = Object.keys(SCENARIO_REGISTRY).sort().join(", ");
      throw new Error(`Unknown scenario '${scenarioName}'. Supported: ${supported}`);
    }
    const sandboxId = `sb_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
    const sandbox: Sandbox = {
      sandboxId,
      scenarioName,
      userId,
      createdAt: new Date().toISOString(),
      status: "active",
    };
    this.#sandboxes.set(sandboxId, sandbox);
    return sandbox;
  }

  getStatus(sandboxId: string): Sandbox | null {
    return this.#sandboxes.get(sandboxId) ?? null;
  }

  list(): Sandbox[] {
    return [...this.#sandboxes.values()].filter((s) => s.status !== "destroyed");
  }

  async run(sandboxId: string, generations = 1): Promise<Record<string, unknown>> {
    const sandbox = this.#sandboxes.get(sandboxId);
    if (!sandbox) throw new Error(`Sandbox ${sandboxId} not found`);
    if (sandbox.status === "destroyed") throw new Error(`Sandbox ${sandboxId} is destroyed`);

    sandbox.status = "running";
    const runId = `${sandboxId}_run_${Date.now()}`;
    sandbox.runId = runId;

    try {
      const { GenerationRunner } = await import("../loop/generation-runner.js");
      const ScenarioClass = SCENARIO_REGISTRY[sandbox.scenarioName];
      if (!ScenarioClass) throw new Error(`Unknown scenario: ${sandbox.scenarioName}`);
      const scenario = new ScenarioClass();
      assertFamilyContract(scenario, "game", `scenario '${sandbox.scenarioName}'`);

      const runner = new GenerationRunner({
        provider: this.#provider,
        scenario,
        store: this.#store,
        runsRoot: this.#runsRoot,
        knowledgeRoot: this.#knowledgeRoot,
        matchesPerGeneration: 2,
      });

      const result = await runner.run(runId, generations);
      sandbox.status = "active";
      return { runId, bestScore: result.bestScore, elo: result.currentElo };
    } catch (err) {
      sandbox.status = "active";
      throw err;
    }
  }

  readPlaybook(sandboxId: string): string {
    const sandbox = this.#sandboxes.get(sandboxId);
    if (!sandbox) {
      throw new Error(`Sandbox ${sandboxId} not found`);
    }
    const artifacts = new ArtifactStore({
      runsRoot: this.#runsRoot,
      knowledgeRoot: this.#knowledgeRoot,
    });
    return artifacts.readPlaybook(sandbox.scenarioName);
  }

  destroy(sandboxId: string): boolean {
    const sandbox = this.#sandboxes.get(sandboxId);
    if (!sandbox) return false;
    sandbox.status = "destroyed";
    this.#sandboxes.delete(sandboxId);
    return true;
  }
}
