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
import { executeGeneratedScenarioSource } from "../scenarios/codegen/executor.js";
import { ArtifactStore } from "./artifact-store.js";
import { exportStrategyPackage, serializeSkillPackage } from "./package.js";
import { SkillPackage } from "./skill-package.js";

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
    const bestRound = result.rounds.find((round) => round.roundNumber === result.bestRound);
    const pkg = new SkillPackage({
      scenarioName: created.name,
      displayName: this.humanizeScenarioName(created.name),
      description: String(created.spec.description ?? `Agent task: ${created.name}`),
      playbook: [
        "## Improvement Summary",
        "",
        `- Best round: ${result.bestRound}`,
        `- Total rounds: ${result.totalRounds}`,
        `- Termination reason: ${result.terminationReason}`,
        `- Best score: ${result.bestScore.toFixed(4)}`,
        "",
        "## Best Output",
        "",
        result.bestOutput,
      ].join("\n"),
      lessons: this.buildAgentTaskLessons(result, bestRound?.reasoning ?? ""),
      bestStrategy: {
        family: "agent_task",
        best_round: result.bestRound,
        termination_reason: result.terminationReason,
      },
      bestScore: result.bestScore,
      bestElo: 1500,
      hints: "",
      metadata: {
        family: "agent_task",
        total_rounds: result.totalRounds,
        termination_reason: result.terminationReason,
        judge_failures: result.judgeFailures,
      },
      taskPrompt: created.spec.taskPrompt,
      judgeRubric: created.spec.rubric,
      exampleOutputs: [{
        output: result.bestOutput,
        score: result.bestScore,
        reasoning: bestRound?.reasoning ?? "Best output from improvement loop.",
      }],
      outputFormat: String(created.spec.outputFormat ?? "free_text"),
      referenceContext: typeof created.spec.referenceContext === "string"
        ? created.spec.referenceContext
        : null,
      contextPreparation: typeof created.spec.contextPreparation === "string"
        ? created.spec.contextPreparation
        : null,
      maxRounds: Number(created.spec.maxRounds ?? created.spec.max_rounds ?? job.generations),
      qualityThreshold: Number(created.spec.qualityThreshold ?? created.spec.quality_threshold ?? 0.9),
    });
    job.result = serializeSkillPackage(pkg);
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
    const execution = await executeGeneratedScenarioSource({
      source,
      family,
      name: created.name,
      maxSteps: Number(created.spec.max_steps ?? created.spec.maxSteps ?? 20),
    });

    job.progress = execution.stepsExecuted;
    job.status = "completed";
    const pkg = new SkillPackage({
      scenarioName: created.name,
      displayName: this.humanizeScenarioName(created.name),
      description: String(created.spec.description ?? `Generated ${family} scenario`),
      playbook: this.buildGeneratedScenarioPlaybook(family, execution),
      lessons: this.buildGeneratedScenarioLessons(execution),
      bestStrategy: {
        family,
        action_trace: execution.records.map((record) => record.action.name),
        steps_executed: execution.stepsExecuted,
      },
      bestScore: execution.score,
      bestElo: 1500,
      hints: "",
      metadata: {
        family,
        generated_source: true,
        steps_executed: execution.stepsExecuted,
        dimension_scores: execution.dimensionScores,
        reasoning: execution.reasoning,
      },
    });
    job.result = serializeSkillPackage(pkg);
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
          family,
          scenario_type: scenarioType,
          ...created.spec,
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
            output_format: String(created.spec.outputFormat ?? "free_text"),
            max_rounds: Number(created.spec.maxRounds ?? created.spec.max_rounds ?? 1),
            quality_threshold: Number(created.spec.qualityThreshold ?? created.spec.quality_threshold ?? 0.9),
            ...(typeof created.spec.referenceContext === "string"
              ? { reference_context: created.spec.referenceContext }
              : {}),
            ...(typeof created.spec.contextPreparation === "string"
              ? { context_preparation: created.spec.contextPreparation }
              : {}),
            ...(typeof created.spec.revisionPrompt === "string"
              ? { revision_prompt: created.spec.revisionPrompt }
              : {}),
            ...(typeof created.spec.sampleInput === "string"
              ? { sample_input: created.spec.sampleInput }
              : {}),
            ...(Array.isArray(created.spec.requiredConcepts)
              ? { required_concepts: created.spec.requiredConcepts }
              : {}),
            ...(Array.isArray(created.spec.referenceSources)
              ? { reference_sources: created.spec.referenceSources }
              : {}),
            ...(Array.isArray(created.spec.requiredContextKeys)
              ? { required_context_keys: created.spec.requiredContextKeys }
              : {}),
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

  private humanizeScenarioName(name: string): string {
    return name.replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
  }

  private buildAgentTaskLessons(result: {
    bestScore: number;
    totalRounds: number;
    terminationReason: string;
  }, bestReasoning: string): string[] {
    const lessons = [
      `The best output reached ${result.bestScore.toFixed(4)} quality after ${result.totalRounds} rounds.`,
      `The loop stopped because '${result.terminationReason}'.`,
    ];
    if (bestReasoning.trim()) {
      lessons.push(bestReasoning.trim());
    }
    return lessons;
  }

  private buildGeneratedScenarioPlaybook(
    family: ScenarioFamilyName,
    execution: {
      score: number;
      reasoning: string;
      dimensionScores: Record<string, number>;
      records: Array<{ action: { name: string } }>;
      stepsExecuted: number;
    },
  ): string {
    const dimensions = Object.entries(execution.dimensionScores)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([name, value]) => `- ${name}: ${value.toFixed(4)}`);
    const actions = execution.records.map((record) => `- ${record.action.name}`);
    return [
      "## Generated Scenario Summary",
      "",
      `- Family: ${family}`,
      `- Score: ${execution.score.toFixed(4)}`,
      `- Steps executed: ${execution.stepsExecuted}`,
      "",
      "## Evaluation Reasoning",
      "",
      execution.reasoning,
      "",
      "## Dimension Scores",
      "",
      ...(dimensions.length > 0 ? dimensions : ["- No dimension scores recorded."]),
      "",
      "## Action Trace",
      "",
      ...(actions.length > 0 ? actions : ["- No executable actions were available."]),
    ].join("\n");
  }

  private buildGeneratedScenarioLessons(execution: {
    reasoning: string;
    dimensionScores: Record<string, number>;
  }): string[] {
    const weakest = Object.entries(execution.dimensionScores)
      .sort(([, left], [, right]) => left - right)[0];
    const lessons = [execution.reasoning];
    if (weakest) {
      lessons.push(`The weakest dimension was '${weakest[0]}' at ${weakest[1].toFixed(4)}.`);
    }
    return lessons;
  }
}
