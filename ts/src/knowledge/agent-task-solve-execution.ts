import { ImprovementLoop } from "../execution/improvement-loop.js";
import { createAgentTask } from "../scenarios/agent-task-factory.js";
import { AgentTaskSpecSchema, type AgentTaskSpec } from "../scenarios/agent-task-spec.js";
import { SolveGenerationBudget } from "./solve-generation-budget.js";
import type {
  AgentTaskInterface,
  ImprovementResult,
  LLMProvider,
} from "../types/index.js";
import { completeWithProviderHooks, type HookBus } from "../extensions/index.js";
import type { SerializedSkillPackageDict } from "./package.js";
import { buildAgentTaskSolvePackage } from "./solve-workflow.js";

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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readRecordArray(
  spec: Record<string, unknown>,
  ...keys: string[]
): Array<Record<string, unknown>> | null {
  for (const key of keys) {
    const value = spec[key];
    if (Array.isArray(value) && value.every(isRecord)) {
      return value;
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

export type AgentTaskSolveTask = AgentTaskInterface & {
  readonly name: string;
  readonly spec: AgentTaskSpec;
};

export interface AgentTaskSolveLoop {
  run(opts: {
    initialOutput: string;
    state: Record<string, unknown>;
    referenceContext?: string;
    requiredConcepts?: string[];
    calibrationExamples?: Array<Record<string, unknown>>;
  }): Promise<ImprovementResult>;
}

export interface AgentTaskSolveExecutionDeps {
  createTask?: (opts: {
    spec: AgentTaskSpec;
    name: string;
    provider: LLMProvider;
    hookBus?: HookBus | null;
  }) => AgentTaskSolveTask;
  createLoop?: (opts: {
    task: AgentTaskSolveTask;
    maxRounds: number;
    qualityThreshold: number;
    timeBudget?: SolveGenerationBudget;
  }) => AgentTaskSolveLoop;
}

export interface AgentTaskSolveExecutionResult {
  progress: number;
  result: SerializedSkillPackageDict;
}

function defaultCreateLoop(opts: {
  task: AgentTaskSolveTask;
  maxRounds: number;
  qualityThreshold: number;
  timeBudget?: SolveGenerationBudget;
}): AgentTaskSolveLoop {
  return new ImprovementLoop({
    task: opts.task,
    maxRounds: opts.maxRounds,
    qualityThreshold: opts.qualityThreshold,
    timeBudget: opts.timeBudget,
  });
}

export async function executeAgentTaskSolve(opts: {
  provider: LLMProvider;
  created: { name: string; spec: Record<string, unknown> };
  generations: number;
  generationTimeBudgetSeconds?: number | null;
  hookBus?: HookBus | null;
  deps?: AgentTaskSolveExecutionDeps;
}): Promise<AgentTaskSolveExecutionResult> {
  const spec = buildAgentTaskSolveSpec(
    {
      ...opts.created.spec,
      maxRounds: opts.generations,
      max_rounds: opts.generations,
    },
    opts.generations,
  );
  const task = (opts.deps?.createTask ?? createAgentTask)({
    spec,
    name: opts.created.name,
    provider: opts.provider,
    hookBus: opts.hookBus ?? null,
  });
  const timeBudget = new SolveGenerationBudget({
    scenarioName: opts.created.name,
    budgetSeconds: opts.generationTimeBudgetSeconds,
  });
  const loop = (opts.deps?.createLoop ?? defaultCreateLoop)({
    task,
    maxRounds: spec.maxRounds,
    qualityThreshold: spec.qualityThreshold,
    timeBudget,
  });

  timeBudget.check("initial state");
  const initialState = task.prepareContext
    ? await task.prepareContext(task.initialState())
    : task.initialState();
  timeBudget.check("context preparation");
  const contextErrors = task.validateContext
    ? task.validateContext(initialState)
    : [];
  timeBudget.check("context validation");
  if (contextErrors.length > 0) {
    throw new Error(`agent_task context preparation failed: ${contextErrors.join("; ")}`);
  }

  timeBudget.check("initial generation");
  const initialOutput = await completeWithProviderHooks({
    hookBus: opts.hookBus ?? null,
    provider: opts.provider,
    role: "agent_task_initial",
    systemPrompt: "You are a helpful assistant.",
    userPrompt: task.getTaskPrompt(initialState),
  });
  timeBudget.check("initial generation");

  const result = await loop.run({
    initialOutput: initialOutput.text,
    state: initialState,
    referenceContext: spec.referenceContext ?? undefined,
    requiredConcepts: spec.requiredConcepts ?? undefined,
    calibrationExamples: spec.calibrationExamples ?? undefined,
  });
  timeBudget.check("improvement loop");

  const bestRound = result.rounds.find((round) => round.roundNumber === result.bestRound);
  return {
    progress: result.totalRounds,
    result: buildAgentTaskSolvePackage({
      scenarioName: opts.created.name,
      description: String(opts.created.spec.description ?? `Agent task: ${opts.created.name}`),
      taskPrompt: spec.taskPrompt,
      judgeRubric: spec.judgeRubric,
      outputFormat: spec.outputFormat,
      maxRounds: spec.maxRounds,
      qualityThreshold: spec.qualityThreshold,
      bestRound: result.bestRound,
      totalRounds: result.totalRounds,
      terminationReason: result.terminationReason,
      bestScore: result.bestScore,
      bestOutput: result.bestOutput,
      judgeFailures: result.judgeFailures,
      bestReasoning: bestRound?.reasoning ?? "Best output from improvement loop.",
      referenceContext: spec.referenceContext ?? null,
      contextPreparation: spec.contextPreparation ?? null,
    }),
  };
}
