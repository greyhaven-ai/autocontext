/**
 * Core types for MTS — mirrors Python dataclasses with Zod validation.
 */

import { z } from "zod";

// ---------------------------------------------------------------------------
// Completion / Provider types
// ---------------------------------------------------------------------------

export const CompletionResultSchema = z.object({
  text: z.string(),
  model: z.string().nullish(),
  usage: z.record(z.number()).default({}),
  costUsd: z.number().nullish(),
});

export type CompletionResult = z.infer<typeof CompletionResultSchema>;

export class ProviderError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ProviderError";
  }
}

export interface LLMProvider {
  complete(opts: {
    systemPrompt: string;
    userPrompt: string;
    model?: string;
    temperature?: number;
    maxTokens?: number;
  }): Promise<CompletionResult>;

  defaultModel(): string;

  readonly name: string;
}

// ---------------------------------------------------------------------------
// Judge types
// ---------------------------------------------------------------------------

export const JudgeResultSchema = z.object({
  score: z.number().min(0).max(1),
  reasoning: z.string(),
  dimensionScores: z.record(z.number().min(0).max(1)).default({}),
  rawResponses: z.array(z.string()).default([]),
  parseMethod: z.enum(["raw_json", "code_block", "markers", "plaintext", "none"]).default("none"),
  internalRetries: z.number().int().min(0).default(0),
  dimensionsWereGenerated: z.boolean().default(false),
});

export type JudgeResult = z.infer<typeof JudgeResultSchema>;

// ---------------------------------------------------------------------------
// Agent task types
// ---------------------------------------------------------------------------

export const AgentTaskResultSchema = z.object({
  score: z.number().min(0).max(1),
  reasoning: z.string(),
  dimensionScores: z.record(z.number().min(0).max(1)).default({}),
  internalRetries: z.number().int().min(0).default(0),
});

export type AgentTaskResult = z.infer<typeof AgentTaskResultSchema>;

export interface AgentTaskInterface {
  getTaskPrompt(state: Record<string, unknown>): string;

  evaluateOutput(
    output: string,
    state: Record<string, unknown>,
    opts?: {
      referenceContext?: string;
      requiredConcepts?: string[];
      calibrationExamples?: Array<Record<string, unknown>>;
      pinnedDimensions?: string[];
    },
  ): Promise<AgentTaskResult>;

  getRubric(): string;

  initialState(seed?: number): Record<string, unknown>;

  describeTask(): string;

  prepareContext?(state: Record<string, unknown>): Promise<Record<string, unknown>>;

  validateContext?(state: Record<string, unknown>): string[];

  reviseOutput?(
    output: string,
    judgeResult: AgentTaskResult,
    state: Record<string, unknown>,
  ): Promise<string>;

  /**
   * Optional: verify factual claims in the output.
   *
   * **Limitation**: Without an override, hallucination detection relies
   * entirely on the LLM judge's training data. The judge catches obvious
   * fabrications but cannot verify claims against external sources.
   * Override to add external verification (web search, DB lookup, etc.)
   * for production use cases involving factual content.
   */
  verifyFacts?(
    output: string,
    state: Record<string, unknown>,
  ): Promise<{ verified: boolean; issues: string[] }>;
}

// ---------------------------------------------------------------------------
// Task queue types
// ---------------------------------------------------------------------------

export const TaskStatusSchema = z.enum(["pending", "running", "completed", "failed"]);
export type TaskStatus = z.infer<typeof TaskStatusSchema>;

export const TaskRowSchema = z.object({
  id: z.string(),
  specName: z.string(),
  status: TaskStatusSchema,
  priority: z.number().int().default(0),
  configJson: z.string().nullish(),
  scheduledAt: z.string().nullish(),
  startedAt: z.string().nullish(),
  completedAt: z.string().nullish(),
  bestScore: z.number().nullish(),
  bestOutput: z.string().nullish(),
  totalRounds: z.number().int().nullish(),
  metThreshold: z.boolean().default(false),
  resultJson: z.string().nullish(),
  error: z.string().nullish(),
  createdAt: z.string(),
  updatedAt: z.string(),
});

export type TaskRow = z.infer<typeof TaskRowSchema>;

// ---------------------------------------------------------------------------
// Improvement loop types
// ---------------------------------------------------------------------------

export const RoundResultSchema = z.object({
  roundNumber: z.number().int(),
  output: z.string(),
  score: z.number(),
  reasoning: z.string(),
  dimensionScores: z.record(z.number()).default({}),
  isRevision: z.boolean().default(false),
  judgeFailed: z.boolean().default(false),
  worstDimension: z.string().nullish(),
  worstDimensionScore: z.number().nullish(),
  roundDurationMs: z.number().int().min(0).nullish(),
});

export type RoundResult = z.infer<typeof RoundResultSchema>;

export const ImprovementResultSchema = z.object({
  rounds: z.array(RoundResultSchema),
  bestOutput: z.string(),
  bestScore: z.number(),
  bestRound: z.number().int(),
  totalRounds: z.number().int(),
  metThreshold: z.boolean(),
  judgeFailures: z.number().int().default(0),
  terminationReason: z
    .enum([
      "threshold_met",
      "max_rounds",
      "plateau_stall",
      "unchanged_output",
      "consecutive_failures",
    ])
    .default("max_rounds"),
  dimensionTrajectory: z.record(z.array(z.number())).default({}),
  totalInternalRetries: z.number().int().min(0).default(0),
  durationMs: z.number().int().min(0).nullish(),
  judgeCalls: z.number().int().min(0).default(0),
});

export type ImprovementResult = z.infer<typeof ImprovementResultSchema>;

// ---------------------------------------------------------------------------
// Notification types
// ---------------------------------------------------------------------------

export const EventTypeSchema = z.enum([
  "threshold_met",
  "regression",
  "completion",
  "failure",
]);
export type EventType = z.infer<typeof EventTypeSchema>;

export const NotificationEventSchema = z.object({
  eventType: EventTypeSchema,
  taskId: z.string(),
  specName: z.string(),
  score: z.number(),
  threshold: z.number().optional(),
  round: z.number().int().optional(),
  message: z.string(),
});

export type NotificationEvent = z.infer<typeof NotificationEventSchema>;
