import {
  ImprovementLoop,
  type ImprovementLoopProgressObserver,
} from "../execution/improvement-loop.js";
import { createAgentTask } from "../scenarios/agent-task-factory.js";
import { completeAgentTaskArtifact } from "../scenarios/agent-task-artifact-completion.js";
import {
  acquireProviderIsolation,
  closeProviderIsolation,
  NO_TOOLS_PROVIDER_ISOLATION,
} from "../providers/provider-isolation.js";
import { AgentTaskSpecSchema, type AgentTaskSpec } from "../scenarios/agent-task-spec.js";
import { SolveGenerationBudget } from "./solve-generation-budget.js";
import type {
  AgentTaskInterface,
  ImprovementResult,
  LLMProvider,
  RoundResult,
} from "../types/index.js";
import type { HookBus } from "../extensions/index.js";
import type { SerializedSkillPackageDict } from "./package.js";
import { buildAgentTaskSolvePackage } from "./solve-workflow.js";
import { assertAgentTaskOutputFormat } from "../scenarios/agent-task-output-format.js";
import {
  buildAgentTaskOutcomeV1,
  type AgentTaskOutcomeV1,
} from "./agent-task-outcome.js";

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
  const improvementTaskContractVersion =
    rawSpec.improvementTaskContractVersion === 1 || rawSpec.improvement_task_contract_version === 1
      ? 1
      : undefined;
  return AgentTaskSpecSchema.parse({
    improvementTaskContractVersion,
    taskDataSources: rawSpec.taskDataSources ?? rawSpec.task_data_sources,
    taskPrompt: readString(rawSpec, "taskPrompt", "task_prompt") ?? "",
    judgeRubric:
      readString(rawSpec, "judgeRubric", "judge_rubric", "rubric") ?? "Evaluate the response.",
    outputFormat:
      outputFormat === "json_schema" || outputFormat === "code" ? outputFormat : "free_text",
    judgeModel: readString(rawSpec, "judgeModel", "judge_model") ?? "",
    difficultyTiers: readRecordArray(rawSpec, "difficultyTiers", "difficulty_tiers"),
    referenceContext: readString(rawSpec, "referenceContext", "reference_context"),
    evaluationContext: readString(rawSpec, "evaluationContext", "evaluation_context"),
    referenceSources: readStringArray(rawSpec, "referenceSources", "reference_sources"),
    requiredConcepts: readStringArray(rawSpec, "requiredConcepts", "required_concepts"),
    calibrationExamples: readRecordArray(rawSpec, "calibrationExamples", "calibration_examples"),
    contextPreparation: readString(rawSpec, "contextPreparation", "context_preparation"),
    requiredContextKeys: readStringArray(rawSpec, "requiredContextKeys", "required_context_keys"),
    minRounds: readNumber(rawSpec, 1, "minRounds", "min_rounds"),
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

export interface AgentTaskSolveProgress {
  phase: "context_preparation" | "draft" | "evaluation" | "revision" | "finalization";
  status: "started" | "completed";
  round?: number;
  /** Finalized per-round result, present on completed evaluations. */
  roundResult?: RoundResult;
  /** Best score retained after the completed evaluation. */
  bestScore?: number;
}

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
    evaluationProvider?: LLMProvider;
    hookBus?: HookBus | null;
  }) => AgentTaskSolveTask;
  createLoop?: (opts: {
    task: AgentTaskSolveTask;
    minRounds: number;
    maxRounds: number;
    qualityThreshold: number;
    timeBudget?: SolveGenerationBudget;
    onProgress?: ImprovementLoopProgressObserver;
  }) => AgentTaskSolveLoop;
}

export interface AgentTaskSolveExecutionResult {
  progress: number;
  result: SerializedSkillPackageDict;
  outcome: AgentTaskOutcomeV1;
}

function defaultCreateLoop(opts: {
  task: AgentTaskSolveTask;
  minRounds: number;
  maxRounds: number;
  qualityThreshold: number;
  timeBudget?: SolveGenerationBudget;
  onProgress?: ImprovementLoopProgressObserver;
}): AgentTaskSolveLoop {
  return new ImprovementLoop({
    task: opts.task,
    minRounds: opts.minRounds,
    maxRounds: opts.maxRounds,
    qualityThreshold: opts.qualityThreshold,
    timeBudget: opts.timeBudget,
    onProgress: opts.onProgress,
  });
}

function reportSolveProgress(
  onProgress: ((progress: AgentTaskSolveProgress) => void | Promise<void>) | undefined,
  progress: AgentTaskSolveProgress,
): void {
  try {
    const result = onProgress?.(progress);
    result?.catch(() => undefined);
  } catch {
    // Progress telemetry must never alter solve results.
  }
}

export async function executeAgentTaskSolve(opts: {
  provider: LLMProvider;
  evaluationProvider?: LLMProvider;
  created: { name: string; spec: Record<string, unknown> };
  generations: number;
  minimumGenerations?: number;
  generationTimeBudgetSeconds?: number | null;
  hookBus?: HookBus | null;
  onProgress?: (progress: AgentTaskSolveProgress) => void | Promise<void>;
  deps?: AgentTaskSolveExecutionDeps;
}): Promise<AgentTaskSolveExecutionResult> {
  const persistedSpec = buildAgentTaskSolveSpec(opts.created.spec, opts.generations);
  const spec = AgentTaskSpecSchema.parse({
    ...persistedSpec,
    minRounds: opts.minimumGenerations ?? persistedSpec.minRounds ?? 1,
    maxRounds: opts.generations,
  });
  const task = (opts.deps?.createTask ?? createAgentTask)({
    spec,
    name: opts.created.name,
    provider: opts.provider,
    ...(opts.evaluationProvider ? { evaluationProvider: opts.evaluationProvider } : {}),
    hookBus: opts.hookBus ?? null,
  });
  const timeBudget = new SolveGenerationBudget({
    scenarioName: opts.created.name,
    budgetSeconds: opts.generationTimeBudgetSeconds,
  });
  const loop = (opts.deps?.createLoop ?? defaultCreateLoop)({
    task,
    minRounds: spec.minRounds ?? 1,
    maxRounds: spec.maxRounds,
    qualityThreshold: spec.qualityThreshold,
    timeBudget,
    onProgress: (progress) => {
      reportSolveProgress(opts.onProgress, progress);
    },
  });

  timeBudget.check("initial state");
  reportSolveProgress(opts.onProgress, {
    phase: "context_preparation",
    status: "started",
  });
  const initialState = task.prepareContext
    ? await task.prepareContext(task.initialState())
    : task.initialState();
  timeBudget.check("context preparation");
  const contextErrors = task.validateContext ? task.validateContext(initialState) : [];
  timeBudget.check("context validation");
  if (contextErrors.length > 0) {
    throw new Error(`agent_task context preparation failed: ${contextErrors.join("; ")}`);
  }
  reportSolveProgress(opts.onProgress, {
    phase: "context_preparation",
    status: "completed",
  });

  timeBudget.check("initial generation");
  reportSolveProgress(opts.onProgress, { phase: "draft", status: "started" });
  const acquiredDraftProvider = spec.evaluationContext?.trim()
    ? acquireProviderIsolation(opts.provider, NO_TOOLS_PROVIDER_ISOLATION)
    : { provider: opts.provider, owned: false };
  let initialOutput;
  try {
    initialOutput = await completeAgentTaskArtifact({
      hookBus: opts.hookBus ?? null,
      provider: acquiredDraftProvider.provider,
      role: "agent_task_initial",
      artifactLabel: "initial response",
      systemPrompt: "You are a helpful assistant.",
      userPrompt: task.getTaskPrompt(initialState),
    });
  } finally {
    closeProviderIsolation(acquiredDraftProvider);
  }
  assertAgentTaskOutputFormat({
    improvementTaskContractVersion: spec.improvementTaskContractVersion,
    outputFormat: spec.outputFormat,
    output: initialOutput.text,
    artifactLabel: "initial response",
  });
  timeBudget.check("initial generation");
  reportSolveProgress(opts.onProgress, { phase: "draft", status: "completed" });

  const result = await loop.run({
    initialOutput: initialOutput.text,
    state: initialState,
    referenceContext: spec.referenceContext ?? undefined,
    requiredConcepts: spec.requiredConcepts ?? undefined,
    calibrationExamples: spec.calibrationExamples ?? undefined,
  });
  timeBudget.check("improvement loop");

  const hasAuthoritativeEvaluation = result.rounds.some((round) => !round.judgeFailed);
  if (spec.improvementTaskContractVersion === 1 && !hasAuthoritativeEvaluation) {
    throw new Error(
      `Structured-v1 agent-task solve '${opts.created.name}' produced no usable authoritative evaluation `
      + `(judge_failures=${result.judgeFailures}, total_rounds=${result.totalRounds}, `
      + `termination_reason=${result.terminationReason})`,
    );
  }

  reportSolveProgress(opts.onProgress, {
    phase: "finalization",
    status: "started",
    round: result.totalRounds,
  });
  const bestRound = result.rounds.find((round) => round.roundNumber === result.bestRound);
  const executionResult = {
    progress: result.totalRounds,
    outcome: buildAgentTaskOutcomeV1({
      result,
      qualityThreshold: spec.qualityThreshold,
      maxIterations: spec.maxRounds,
    }),
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
  reportSolveProgress(opts.onProgress, {
    phase: "finalization",
    status: "completed",
    round: result.totalRounds,
  });
  return executionResult;
}
