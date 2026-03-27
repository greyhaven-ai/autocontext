/**
 * Solve-on-demand manager — submit, track, and retrieve solve jobs (AC-370).
 * Mirrors Python's autocontext/knowledge/solver.py.
 */

import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { LLMProvider } from "../types/index.js";
import type { SQLiteStore } from "../storage/index.js";
import { assertFamilyContract } from "../scenarios/family-interfaces.js";
import { AgentTaskSpecSchema, type AgentTaskSpec } from "../scenarios/agent-task-spec.js";
import { getScenarioTypeMarker, type ScenarioFamilyName } from "../scenarios/families.js";
import {
  generateAndValidateScenarioSource,
  hasCodegen,
  CodegenUnsupportedFamilyError,
} from "../scenarios/codegen/index.js";
import { executeGeneratedScenarioSource } from "../scenarios/codegen/executor.js";
import { healSpec } from "../scenarios/spec-auto-heal.js";
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

function readString(spec: Record<string, unknown>, ...keys: string[]): string | null {
  for (const key of keys) {
    const value = spec[key];
    if (typeof value === "string" && value.trim().length > 0) {
      return value.trim();
    }
  }
  return null;
}

function readStringArray(spec: Record<string, unknown>, ...keys: string[]): string[] | null {
  for (const key of keys) {
    const value = spec[key];
    if (Array.isArray(value) && value.every((entry) => typeof entry === "string")) {
      return value;
    }
  }
  return null;
}

function readRecordArray(
  spec: Record<string, unknown>,
  ...keys: string[]
): Array<Record<string, unknown>> | null {
  for (const key of keys) {
    const value = spec[key];
    if (Array.isArray(value) && value.every((entry) => entry != null && typeof entry === "object")) {
      return value as Array<Record<string, unknown>>;
    }
  }
  return null;
}

function readNumber(spec: Record<string, unknown>, fallback: number, ...keys: string[]): number {
  for (const key of keys) {
    const value = spec[key];
    if (typeof value === "number" && Number.isFinite(value)) {
      return value;
    }
    if (typeof value === "string" && value.trim()) {
      const parsed = Number(value);
      if (!Number.isNaN(parsed)) {
        return parsed;
      }
    }
  }
  return fallback;
}

export function buildAgentTaskSolveSpec(
  rawSpec: Record<string, unknown>,
  fallbackRounds: number,
): AgentTaskSpec {
  const outputFormat = readString(rawSpec, "outputFormat", "output_format");
  return AgentTaskSpecSchema.parse({
    taskPrompt: readString(rawSpec, "taskPrompt", "task_prompt") ?? "",
    judgeRubric: readString(rawSpec, "judgeRubric", "judge_rubric", "rubric") ?? "Evaluate the response.",
    outputFormat: outputFormat === "json_schema" || outputFormat === "code" ? outputFormat : "free_text",
    judgeModel: readString(rawSpec, "judgeModel", "judge_model") ?? "",
    difficultyTiers: readRecordArray(rawSpec, "difficultyTiers", "difficulty_tiers"),
    referenceContext: readString(rawSpec, "referenceContext", "reference_context"),
    referenceSources: readStringArray(rawSpec, "referenceSources", "reference_sources"),
    requiredConcepts: readStringArray(rawSpec, "requiredConcepts", "required_concepts"),
    calibrationExamples: readRecordArray(rawSpec, "calibrationExamples", "calibration_examples"),
    contextPreparation: readString(rawSpec, "contextPreparation", "context_preparation"),
    requiredContextKeys: readStringArray(rawSpec, "requiredContextKeys", "required_context_keys"),
    maxRounds: readNumber(rawSpec, fallbackRounds, "maxRounds", "max_rounds"),
    qualityThreshold: readNumber(rawSpec, 0.9, "qualityThreshold", "quality_threshold"),
    revisionPrompt: readString(rawSpec, "revisionPrompt", "revision_prompt"),
    sampleInput: readString(rawSpec, "sampleInput", "sample_input"),
  });
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
      const family = this.coerceFamily(created.family);
      const prepared = {
        ...created,
        family,
        spec: healSpec(created.spec as Record<string, unknown>, family, job.description) as typeof created.spec,
      };
      job.scenarioName = prepared.name;
      job.family = prepared.family;

      // Check if this matches a built-in game scenario first (AC-436)
      const { SCENARIO_REGISTRY } = await import("../scenarios/registry.js");
      if (prepared.name in SCENARIO_REGISTRY) {
        await this.runGameScenario(job, prepared.name);
      } else if (family === "game") {
        // Family is "game" but not in the built-in registry — persist and fail
        this.persistScenarioScaffold(prepared);
        throw new Error(
          `Game scenario '${prepared.name}' not found in SCENARIO_REGISTRY. ` +
          `Built-in game scenarios: ${Object.keys(SCENARIO_REGISTRY).join(", ")}`,
        );
      } else if (family === "agent_task") {
        this.persistScenarioScaffold(prepared);
        await this.runAgentTaskScenario(job, prepared);
      } else if (hasCodegen(family)) {
        this.persistScenarioScaffold(prepared);
        await this.runCodegenScenario(job, prepared, family);
      } else {
        this.persistScenarioScaffold(prepared);
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
    created: { name: string; spec: Record<string, unknown> },
  ): Promise<void> {
    job.status = "running";
    const { ImprovementLoop } = await import("../execution/improvement-loop.js");
    const { createAgentTask } = await import("../scenarios/agent-task-factory.js");
    const spec = buildAgentTaskSolveSpec(created.spec, job.generations);

    const task = createAgentTask({
      spec,
      name: created.name,
      provider: this.provider,
    });

    const loop = new ImprovementLoop({
      task,
      maxRounds: spec.maxRounds,
      qualityThreshold: spec.qualityThreshold,
    });

    const initialState = task.prepareContext
      ? await task.prepareContext(task.initialState())
      : task.initialState();
    const contextErrors = task.validateContext
      ? task.validateContext(initialState)
      : [];
    if (contextErrors.length > 0) {
      throw new Error(`agent_task context preparation failed: ${contextErrors.join("; ")}`);
    }
    const initialOutput = await this.provider.complete({
      systemPrompt: "You are a helpful assistant.",
      userPrompt: task.getTaskPrompt(initialState),
    });

    const result = await loop.run({
      initialOutput: initialOutput.text,
      state: initialState,
      referenceContext: spec.referenceContext ?? undefined,
      requiredConcepts: spec.requiredConcepts ?? undefined,
      calibrationExamples: spec.calibrationExamples ?? undefined,
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
      taskPrompt: spec.taskPrompt,
      judgeRubric: spec.judgeRubric,
      exampleOutputs: [{
        output: result.bestOutput,
        score: result.bestScore,
        reasoning: bestRound?.reasoning ?? "Best output from improvement loop.",
      }],
      outputFormat: spec.outputFormat,
      referenceContext: spec.referenceContext ?? null,
      contextPreparation: spec.contextPreparation ?? null,
      maxRounds: spec.maxRounds,
      qualityThreshold: spec.qualityThreshold,
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
    // Generate executable JS source from spec and fail fast if it does not
    // survive a real method-execution sanity pass.
    const { source, validation } = await generateAndValidateScenarioSource(
      family,
      created.spec,
      created.name,
    );

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
        execution_validation: {
          duration_ms: validation.durationMs,
          executed_methods: validation.executedMethods,
        },
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
      const agentTaskSpec = buildAgentTaskSolveSpec(created.spec as Record<string, unknown>, 1);
      writeFileSync(
        join(scenarioDir, "agent_task_spec.json"),
        JSON.stringify(
          {
            task_prompt: agentTaskSpec.taskPrompt,
            judge_rubric: agentTaskSpec.judgeRubric,
            output_format: agentTaskSpec.outputFormat,
            max_rounds: agentTaskSpec.maxRounds,
            quality_threshold: agentTaskSpec.qualityThreshold,
            ...(typeof agentTaskSpec.referenceContext === "string"
              ? { reference_context: agentTaskSpec.referenceContext }
              : {}),
            ...(typeof agentTaskSpec.contextPreparation === "string"
              ? { context_preparation: agentTaskSpec.contextPreparation }
              : {}),
            ...(typeof agentTaskSpec.revisionPrompt === "string"
              ? { revision_prompt: agentTaskSpec.revisionPrompt }
              : {}),
            ...(typeof agentTaskSpec.sampleInput === "string"
              ? { sample_input: agentTaskSpec.sampleInput }
              : {}),
            ...(Array.isArray(agentTaskSpec.requiredConcepts)
              ? { required_concepts: agentTaskSpec.requiredConcepts }
              : {}),
            ...(Array.isArray(agentTaskSpec.referenceSources)
              ? { reference_sources: agentTaskSpec.referenceSources }
              : {}),
            ...(Array.isArray(agentTaskSpec.requiredContextKeys)
              ? { required_context_keys: agentTaskSpec.requiredContextKeys }
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
