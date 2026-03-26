/**
 * Solve-on-demand manager — submit, track, and retrieve solve jobs (AC-370).
 * Mirrors Python's autocontext/knowledge/solver.py.
 */

import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { LLMProvider } from "../types/index.js";
import type { SQLiteStore } from "../storage/index.js";
import { assertFamilyContract } from "../scenarios/family-interfaces.js";
import { getScenarioTypeMarker, type ScenarioFamilyName } from "../scenarios/families.js";
import { generateScenarioSource, hasCodegen, CodegenUnsupportedFamilyError } from "../scenarios/codegen/index.js";
import { ArtifactStore } from "./artifact-store.js";
import { exportStrategyPackage } from "./package.js";

export interface SolveManagerOpts {
  provider: LLMProvider;
  store: SQLiteStore;
  runsRoot: string;
  knowledgeRoot: string;
}

export interface SolveJob {
  jobId: string;
  description: string;
  generations: number;
  status: "pending" | "creating_scenario" | "running" | "completed" | "failed";
  scenarioName?: string;
  family?: string;
  progress?: number;
  result?: Record<string, unknown>;
  error?: string;
}

export class SolveManager {
  private provider: LLMProvider;
  private store: SQLiteStore;
  private runsRoot: string;
  private knowledgeRoot: string;
  private jobs = new Map<string, SolveJob>();

  constructor(opts: SolveManagerOpts) {
    this.provider = opts.provider;
    this.store = opts.store;
    this.runsRoot = opts.runsRoot;
    this.knowledgeRoot = opts.knowledgeRoot;
  }

  submit(description: string, generations: number): string {
    const jobId = `solve_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
    const job: SolveJob = {
      jobId,
      description,
      generations,
      status: "pending",
    };
    this.jobs.set(jobId, job);

    // Fire and forget — run in background
    this.runJob(job).catch((err) => {
      job.status = "failed";
      job.error = err instanceof Error ? err.message : String(err);
    });

    return jobId;
  }

  getStatus(jobId: string): Record<string, unknown> {
    const job = this.jobs.get(jobId);
    if (!job) return { status: "not_found", jobId, error: `Job '${jobId}' not found` };
    return {
      jobId,
      status: job.status,
      description: job.description,
      scenarioName: job.scenarioName ?? null,
      family: job.family ?? null,
      generations: job.generations,
      progress: job.progress ?? 0,
      error: job.error,
    };
  }

  getResult(jobId: string): Record<string, unknown> | null {
    const job = this.jobs.get(jobId);
    if (!job || job.status !== "completed") return null;
    return job.result ?? null;
  }

  private async runJob(job: SolveJob): Promise<void> {
    job.status = "creating_scenario";
    try {
      const { createScenarioFromDescription } = await import("../scenarios/scenario-creator.js");
      const created = await createScenarioFromDescription(job.description, this.provider);
      job.scenarioName = created.name;
      job.family = created.family;
      const family = this.coerceFamily(created.family);

      // Check if this matches a built-in game scenario first (AC-436)
      const { SCENARIO_REGISTRY } = await import("../scenarios/registry.js");
      if (created.name in SCENARIO_REGISTRY) {
        await this.runGameScenario(job, created.name);
      } else if (family === "game") {
        // Family is "game" but not in the built-in registry — persist and fail
        this.persistScenarioScaffold(created);
        throw new Error(
          `Game scenario '${created.name}' not found in SCENARIO_REGISTRY. ` +
          `Built-in game scenarios: ${Object.keys(SCENARIO_REGISTRY).join(", ")}`,
        );
      } else if (family === "agent_task") {
        this.persistScenarioScaffold(created);
        await this.runAgentTaskScenario(job, created);
      } else if (hasCodegen(family)) {
        this.persistScenarioScaffold(created);
        await this.runCodegenScenario(job, created, family);
      } else {
        this.persistScenarioScaffold(created);
        throw new CodegenUnsupportedFamilyError(family);
      }
    } catch (err) {
      job.status = "failed";
      job.error = err instanceof Error ? err.message : String(err);
    }
  }

  /**
   * Run a game-family scenario via GenerationRunner (existing path).
   */
  private async runGameScenario(
    job: SolveJob,
    scenarioName: string,
  ): Promise<void> {
    const { GenerationRunner } = await import("../loop/generation-runner.js");
    const { SCENARIO_REGISTRY } = await import("../scenarios/registry.js");

    const ScenarioClass = SCENARIO_REGISTRY[scenarioName];
    if (!ScenarioClass) {
      throw new Error(`Game scenario '${scenarioName}' not found in SCENARIO_REGISTRY`);
    }

    job.status = "running";
    const scenario = new ScenarioClass();
    assertFamilyContract(scenario, "game", `scenario '${scenarioName}'`);
    const runner = new GenerationRunner({
      provider: this.provider,
      scenario,
      store: this.store,
      runsRoot: this.runsRoot,
      knowledgeRoot: this.knowledgeRoot,
      matchesPerGeneration: 2,
      maxRetries: 0,
      minDelta: 0,
    });

    const runId = `solve_${scenarioName}_${job.jobId}`;
    const result = await runner.run(runId, job.generations);
    job.progress = result.generationsCompleted;
    const artifacts = new ArtifactStore({ runsRoot: this.runsRoot, knowledgeRoot: this.knowledgeRoot });
    job.status = "completed";
    job.result = exportStrategyPackage({ scenarioName, artifacts, store: this.store });
  }

  /**
   * Run an agent-task scenario via ImprovementLoop (existing path).
   */
  private async runAgentTaskScenario(
    job: SolveJob,
    created: { name: string; spec: { taskPrompt: string; rubric: string; [key: string]: unknown } },
  ): Promise<void> {
    job.status = "running";
    const { ImprovementLoop } = await import("../execution/improvement-loop.js");
    const { createAgentTask } = await import("../scenarios/agent-task-factory.js");

    const task = createAgentTask({
      spec: {
        taskPrompt: created.spec.taskPrompt,
        judgeRubric: created.spec.rubric,
        outputFormat: "free_text",
        judgeModel: "",
        maxRounds: Number(created.spec.maxRounds ?? created.spec.max_rounds ?? job.generations),
        qualityThreshold: Number(created.spec.qualityThreshold ?? created.spec.quality_threshold ?? 0.9),
      },
      name: created.name,
      provider: this.provider,
    });

    const loop = new ImprovementLoop({
      task,
      maxRounds: Number(created.spec.maxRounds ?? created.spec.max_rounds ?? job.generations),
      qualityThreshold: Number(created.spec.qualityThreshold ?? created.spec.quality_threshold ?? 0.9),
    });

    const initialState = task.initialState();
    const initialOutput = await this.provider.complete({
      systemPrompt: "You are a helpful assistant.",
      userPrompt: created.spec.taskPrompt,
    });

    const result = await loop.run({
      initialOutput: initialOutput.text,
      state: initialState,
    });

    job.progress = result.totalRounds;
    job.status = "completed";
    job.result = {
      scenarioName: created.name,
      family: "agent_task",
      bestScore: result.bestScore,
      finalOutput: result.bestOutput,
      roundsCompleted: result.totalRounds,
    };
  }

  /**
   * Run a codegen-supported scenario via ScenarioRuntime + secure-exec (AC-436).
   * Generates JS source from the spec, persists it, loads via V8 isolate,
   * and executes a basic evaluation loop.
   */
  private async runCodegenScenario(
    job: SolveJob,
    created: { name: string; family: string; spec: Record<string, unknown> },
    family: ScenarioFamilyName,
  ): Promise<void> {
    // Generate executable JS source from spec
    const source = generateScenarioSource(family, created.spec, created.name);

    // Persist the generated source
    const scenarioDir = join(this.knowledgeRoot, "_custom_scenarios", created.name);
    if (!existsSync(scenarioDir)) {
      mkdirSync(scenarioDir, { recursive: true });
    }
    writeFileSync(join(scenarioDir, "scenario.js"), source, "utf-8");

    job.status = "running";

    // Load and run via ScenarioRuntime
    const { ScenarioRuntime } = await import("../scenarios/codegen/runtime.js");
    const runtime = new ScenarioRuntime();
    try {
      const proxy = await runtime.loadScenario(source, family, created.name);

      // Run a basic evaluation: initialize state, execute available actions, evaluate
      let state = await proxy.call<Record<string, unknown>>("initialState", 42);
      let steps = 0;
      const maxSteps = Number(created.spec.max_steps ?? created.spec.maxSteps ?? 20);

      while (steps < maxSteps) {
        const terminal = await proxy.call<boolean>("isTerminal", state);
        if (terminal) break;

        const actions = await proxy.call<Array<{ name: string }>>("getAvailableActions", state);
        if (!actions || actions.length === 0) break;

        // Execute the first available action
        const actionResult = await proxy.call<{ result: Record<string, unknown>; state: Record<string, unknown> }>(
          "executeAction", state, { name: actions[0].name, parameters: {} },
        );
        state = actionResult.state;
        steps++;
      }

      const result = await proxy.call<{ score: number; reasoning: string; dimensionScores: Record<string, number> }>(
        "getResult", state, { records: [] },
      );

      job.progress = steps;
      job.status = "completed";
      job.result = {
        scenarioName: created.name,
        family,
        score: result.score,
        reasoning: result.reasoning,
        dimensionScores: result.dimensionScores,
        stepsExecuted: steps,
      };
    } finally {
      runtime.dispose();
    }
  }

  private persistScenarioScaffold(created: {
    name: string;
    family: string;
    spec: {
      taskPrompt: string;
      rubric: string;
      description: string;
      [key: string]: unknown;
    };
  }): void {
    const family = this.coerceFamily(created.family);
    const scenarioDir = join(this.knowledgeRoot, "_custom_scenarios", created.name);
    if (!existsSync(scenarioDir)) {
      mkdirSync(scenarioDir, { recursive: true });
    }

    const scenarioType = getScenarioTypeMarker(family);
    writeFileSync(join(scenarioDir, "scenario_type.txt"), scenarioType, "utf-8");
    writeFileSync(
      join(scenarioDir, "spec.json"),
      JSON.stringify(
        {
          name: created.name,
          scenario_type: scenarioType,
          description: created.spec.description,
          taskPrompt: created.spec.taskPrompt,
          rubric: created.spec.rubric,
        },
        null,
        2,
      ),
      "utf-8",
    );

    if (family === "agent_task") {
      writeFileSync(
        join(scenarioDir, "agent_task_spec.json"),
        JSON.stringify(
          {
            task_prompt: created.spec.taskPrompt,
            judge_rubric: created.spec.rubric,
            output_format: "free_text",
            max_rounds: 1,
            quality_threshold: 0.9,
          },
          null,
          2,
        ),
        "utf-8",
      );
    }
  }

  private coerceFamily(family: string): ScenarioFamilyName {
    switch (family) {
      case "simulation":
      case "artifact_editing":
      case "investigation":
      case "workflow":
      case "schema_evolution":
      case "tool_fragility":
      case "negotiation":
      case "operator_loop":
      case "coordination":
      case "agent_task":
        return family;
      default:
        return "agent_task";
    }
  }
}
