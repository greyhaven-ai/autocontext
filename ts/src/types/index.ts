/**
 * Core types for autocontext — mirrors Python dataclasses with Zod validation.
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
  // AC-904: why generation stopped ("max_tokens"/"length" indicates
  // truncation); absent when the provider does not report one.
  stopReason: z.string().nullish(),
  metadata: z.record(z.unknown()).optional(),
  // AC-929: whether the backend actually constrained generation to the
  // requested schema. Mirrors Python's CompletionResult.constrained (AC-913).
  //
  // Optional rather than .default(false): a default makes the field REQUIRED on
  // the inferred output type, so every construction site -- including
  // LLMProvider implementations written outside this repo, since this is public
  // API -- would stop compiling. Absent and false both mean unconstrained, so
  // read it through wasConstrained() in agents/role-schemas.ts, which keeps
  // the `=== true` comparison in exactly one place.
  constrained: z.boolean().optional(),
  // Explicit model-authored scratchpad entries captured from structured
  // deep_think tool calls. They are deliberately separate from user-visible
  // text and optional for backward compatibility with third-party providers.
  thinkingStream: z.array(z.string()).optional(),
  thinkingTool: z.string().nullish(),
  thinkingCapture: z.enum(["tool", "unsupported"]).optional(),
});

export type CompletionResult = z.infer<typeof CompletionResultSchema>;

export class ProviderError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ProviderError";
  }
}

/**
 * A JSON Schema the backend should constrain generation to (AC-929).
 *
 * Passing one is a request, not a guarantee: backends that cannot enforce a
 * schema ignore it and report `constrained: false`. Callers must read that
 * flag rather than assume the text validates -- an unconstrained run should be
 * visible, not inferred.
 */
export interface OutputSchema {
  name: string;
  schema: Record<string, unknown>;
}

export interface CompletionOptions {
  systemPrompt: string;
  userPrompt: string;
  model?: string;
  temperature?: number;
  maxTokens?: number;
  /**
   * Optional and best-effort. An implementation that cannot honor it must
   * still answer and must leave `constrained` false.
   */
  outputSchema?: OutputSchema;
}

export interface ThinkingCompletionOptions extends CompletionOptions {
  /** External scratchpad budget; native provider reasoning is disabled when supported. */
  reasoningEffort?: string;
  maxToolTurns?: number;
}

export interface LLMProvider {
  complete(opts: CompletionOptions): Promise<CompletionResult>;

  /**
   * Optional on purpose: existing third-party providers remain source
   * compatible. Callers use completeWithThinkingFallback() to receive honest
   * `unsupported` provenance when this operation is absent.
   */
  completeWithThinking?(opts: ThinkingCompletionOptions): Promise<CompletionResult>;

  defaultModel(): string;

  close?(): void;

  readonly supportsConcurrentRequests?: boolean;

  readonly supportsThinkingStream?: boolean;

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
  parseMethod: z
    .enum(["raw_json", "code_block", "markers", "plaintext", "none", "delegated", "callback"])
    .default("none"),
  internalRetries: z.number().int().min(0).default(0),
  dimensionsWereGenerated: z.boolean().default(false),
  // AC-885: content-addressed identity of the evaluator (rubric + judge) that produced this
  // score; null for legacy/delegated results. Always present so baselines never silently
  // compare across evaluator changes.
  evaluatorEpoch: z.string().nullable().default(null),
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
  // AC-885: evaluator epoch carried from the judge so the improve loop refuses to
  // compare scores across evaluator changes. null for legacy/delegated results.
  evaluatorEpoch: z.string().nullable().default(null),
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
  // AC-885: evaluator epoch of the round's score (see AgentTaskResultSchema).
  evaluatorEpoch: z.string().nullable().default(null),
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
  // AC-885: evaluator epoch of the winning (best) round (see AgentTaskResultSchema).
  evaluatorEpoch: z.string().nullable().default(null),
  // AC-902: hit/miss accounting for the unchanged-artifact verdict cache.
  verifierCache: z
    .object({ hits: z.number().int(), misses: z.number().int(), entries: z.number().int() })
    .optional(),
});

export type ImprovementResult = z.infer<typeof ImprovementResultSchema>;

// ---------------------------------------------------------------------------
// Notification types
// ---------------------------------------------------------------------------

export const EventTypeSchema = z.enum(["threshold_met", "regression", "completion", "failure"]);
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
