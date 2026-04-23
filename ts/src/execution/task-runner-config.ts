import { randomUUID } from "node:crypto";
import { z } from "zod";

import type { ImprovementResult } from "../types/index.js";
import type { DelegatedResult } from "../judge/delegated.js";
import {
  RlmTaskConfigSchema,
  type RlmSessionRecord,
  type RlmTaskConfig,
} from "../rlm/types.js";
import type { SQLiteStore } from "../storage/index.js";

export interface TaskConfig {
  maxRounds?: number;
  qualityThreshold?: number;
  minRounds?: number;
  referenceContext?: string;
  browserUrl?: string;
  requiredConcepts?: string[];
  calibrationExamples?: Array<Record<string, unknown>>;
  initialOutput?: string;
  rubric?: string;
  taskPrompt?: string;
  revisionPrompt?: string;
  delegatedResults?: DelegatedResult[];
  rlm?: RlmTaskConfig;
}

export interface EnqueueTaskRequest {
  taskPrompt?: string;
  rubric?: string;
  referenceContext?: string;
  browserUrl?: string;
  requiredConcepts?: string[];
  maxRounds?: number;
  qualityThreshold?: number;
  minRounds?: number;
  initialOutput?: string;
  delegatedResults?: DelegatedResult[];
  priority?: number;
  rlmEnabled?: boolean;
  rlmModel?: string;
  rlmMaxTurns?: number;
  rlmMaxTokensPerTurn?: number;
  rlmTemperature?: number;
  rlmMaxStdoutChars?: number;
  rlmCodeTimeoutMs?: number;
  rlmMemoryLimitMb?: number;
}

const DelegatedResultSchema = z.object({
  score: z.number().min(0).max(1),
  reasoning: z.string(),
  dimension_scores: z.record(z.number().min(0).max(1)).optional(),
  dimensionScores: z.record(z.number().min(0).max(1)).optional(),
}).passthrough();

const TaskConfigSchema = z.object({
  max_rounds: z.number().int().positive().optional(),
  quality_threshold: z.number().min(0).max(1).optional(),
  min_rounds: z.number().int().positive().optional(),
  reference_context: z.string().optional(),
  browser_url: z.string().url().optional(),
  required_concepts: z.array(z.string()).optional(),
  calibration_examples: z.array(z.record(z.unknown())).optional(),
  initial_output: z.string().optional(),
  rubric: z.string().optional(),
  task_prompt: z.string().optional(),
  revision_prompt: z.string().optional(),
  delegated_results: z.array(DelegatedResultSchema).optional(),
  rlm_enabled: z.boolean().optional(),
  rlm_model: z.string().optional(),
  rlm_max_turns: z.number().int().positive().optional(),
  rlm_max_tokens_per_turn: z.number().int().positive().optional(),
  rlm_temperature: z.number().min(0).max(2).optional(),
  rlm_max_stdout_chars: z.number().int().positive().optional(),
  rlm_code_timeout_ms: z.number().int().positive().optional(),
  rlm_memory_limit_mb: z.number().int().positive().optional(),
}).passthrough();

export function resolveRlmConfig(raw: Partial<RlmTaskConfig> | null | undefined): RlmTaskConfig | null {
  if (!raw?.enabled) return null;
  return RlmTaskConfigSchema.parse(raw);
}

export function parseTaskConfig(json: string | null): TaskConfig {
  if (!json) return {};
  const raw = JSON.parse(json) as Record<string, unknown>;
  const parsed = TaskConfigSchema.parse(raw);
  return {
    maxRounds: parsed.max_rounds,
    qualityThreshold: parsed.quality_threshold,
    minRounds: parsed.min_rounds,
    referenceContext: parsed.reference_context,
    browserUrl: parsed.browser_url,
    requiredConcepts: parsed.required_concepts,
    calibrationExamples: parsed.calibration_examples,
    initialOutput: parsed.initial_output,
    rubric: parsed.rubric,
    taskPrompt: parsed.task_prompt,
    revisionPrompt: parsed.revision_prompt,
    delegatedResults: parsed.delegated_results?.map((result) => ({
      score: result.score,
      reasoning: result.reasoning,
      dimensionScores: result.dimension_scores ?? result.dimensionScores ?? {},
    })),
    rlm: resolveRlmConfig({
      enabled: parsed.rlm_enabled ?? false,
      model: parsed.rlm_model,
      maxTurns: parsed.rlm_max_turns,
      maxTokensPerTurn: parsed.rlm_max_tokens_per_turn,
      temperature: parsed.rlm_temperature,
      maxStdoutChars: parsed.rlm_max_stdout_chars,
      codeTimeoutMs: parsed.rlm_code_timeout_ms,
      memoryLimitMb: parsed.rlm_memory_limit_mb,
    }) ?? undefined,
  };
}

export function serializeTaskResult(
  result: ImprovementResult,
  rlmSessions?: RlmSessionRecord[],
): string {
  return JSON.stringify({
    rounds: result.rounds.map((round) => ({
      round_number: round.roundNumber,
      score: round.score,
      reasoning: round.reasoning,
      dimension_scores: round.dimensionScores,
      is_revision: round.isRevision,
    })),
    best_score: result.bestScore,
    best_round: result.bestRound,
    total_rounds: result.totalRounds,
    met_threshold: result.metThreshold,
    ...(result.durationMs != null ? { duration_ms: result.durationMs } : {}),
    ...(result.judgeCalls ? { judge_calls: result.judgeCalls } : {}),
    ...(rlmSessions && rlmSessions.length > 0 ? { rlm_sessions: rlmSessions } : {}),
  });
}

export function buildEnqueueTaskConfig(opts?: EnqueueTaskRequest): Record<string, unknown> | undefined {
  const config: Record<string, unknown> = {};
  if (opts?.maxRounds != null) config.max_rounds = opts.maxRounds;
  if (opts?.qualityThreshold != null) config.quality_threshold = opts.qualityThreshold;
  if (opts?.minRounds != null) config.min_rounds = opts.minRounds;
  if (opts?.taskPrompt) config.task_prompt = opts.taskPrompt;
  if (opts?.rubric) config.rubric = opts.rubric;
  if (opts?.referenceContext) config.reference_context = opts.referenceContext;
  if (opts?.browserUrl) config.browser_url = opts.browserUrl;
  if (opts?.requiredConcepts) config.required_concepts = opts.requiredConcepts;
  if (opts?.initialOutput) config.initial_output = opts.initialOutput;
  if (opts?.delegatedResults?.length) {
    config.delegated_results = opts.delegatedResults.map((result) => ({
      score: result.score,
      reasoning: result.reasoning,
      dimension_scores: result.dimensionScores ?? {},
    }));
  }
  if (opts?.rlmEnabled != null) config.rlm_enabled = opts.rlmEnabled;
  if (opts?.rlmModel) config.rlm_model = opts.rlmModel;
  if (opts?.rlmMaxTurns != null) config.rlm_max_turns = opts.rlmMaxTurns;
  if (opts?.rlmMaxTokensPerTurn != null) config.rlm_max_tokens_per_turn = opts.rlmMaxTokensPerTurn;
  if (opts?.rlmTemperature != null) config.rlm_temperature = opts.rlmTemperature;
  if (opts?.rlmMaxStdoutChars != null) config.rlm_max_stdout_chars = opts.rlmMaxStdoutChars;
  if (opts?.rlmCodeTimeoutMs != null) config.rlm_code_timeout_ms = opts.rlmCodeTimeoutMs;
  if (opts?.rlmMemoryLimitMb != null) config.rlm_memory_limit_mb = opts.rlmMemoryLimitMb;
  return Object.keys(config).length > 0 ? config : undefined;
}

export function enqueueConfiguredTask(
  store: SQLiteStore,
  specName: string,
  opts?: EnqueueTaskRequest,
): string {
  const taskId = randomUUID();
  store.enqueueTask(
    taskId,
    specName,
    opts?.priority ?? 0,
    buildEnqueueTaskConfig(opts),
  );
  return taskId;
}
