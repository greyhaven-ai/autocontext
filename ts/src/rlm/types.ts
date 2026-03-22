/**
 * Core types for the RLM (REPL-Loop Mode) module.
 * Mirrors Python autocontext.harness.repl.types with Zod-first validation.
 */

import { z } from "zod";

// ---------------------------------------------------------------------------
// Zod schemas
// ---------------------------------------------------------------------------

export const ReplCommandSchema = z.object({
  code: z.string(),
});

export const ReplResultSchema = z.object({
  stdout: z.string(),
  error: z.string().nullable().default(null),
  answer: z.record(z.unknown()).default({}),
});

export const ExecutionRecordSchema = z.object({
  turn: z.number().int(),
  code: z.string(),
  stdout: z.string(),
  error: z.string().nullable().default(null),
  answerReady: z.boolean().default(false),
});

export const RlmContextSchema = z.object({
  variables: z.record(z.unknown()),
  summary: z.string(),
});

export const RlmTaskConfigSchema = z.object({
  enabled: z.boolean().default(false),
  model: z.string().optional(),
  maxTurns: z.number().int().positive().max(25).default(6),
  maxTokensPerTurn: z.number().int().positive().max(8192).default(2048),
  temperature: z.number().min(0).max(2).default(0.2),
  maxStdoutChars: z.number().int().positive().max(65536).default(8192),
  codeTimeoutMs: z.number().int().positive().max(60000).default(10000),
  memoryLimitMb: z.number().int().positive().max(512).default(64),
});

export const RlmPhaseSchema = z.enum(["generate", "revise"]);

export const RlmSessionRecordSchema = z.object({
  phase: RlmPhaseSchema,
  backend: z.literal("secure_exec").default("secure_exec"),
  content: z.string(),
  turnsUsed: z.number().int().min(0),
  executionHistory: z.array(ExecutionRecordSchema),
  error: z.string().nullish(),
});

// ---------------------------------------------------------------------------
// Inferred TypeScript types
// ---------------------------------------------------------------------------

export type ReplCommand = z.infer<typeof ReplCommandSchema>;
export type ReplResult = z.infer<typeof ReplResultSchema>;
export type ExecutionRecord = z.infer<typeof ExecutionRecordSchema>;
export type RlmContext = z.infer<typeof RlmContextSchema>;
export type RlmTaskConfig = z.infer<typeof RlmTaskConfigSchema>;
export type RlmPhase = z.infer<typeof RlmPhaseSchema>;
export type RlmSessionRecord = z.infer<typeof RlmSessionRecordSchema>;

// ---------------------------------------------------------------------------
// Interfaces
// ---------------------------------------------------------------------------

/** Protocol for REPL workers (exec-based and Monty-based). */
export interface ReplWorker {
  readonly namespace: Record<string, unknown>;
  runCode(command: ReplCommand): ReplResult | Promise<ReplResult>;
}

/** LLM completion function signature for RLM multi-turn sessions. */
export type LlmComplete = (
  messages: Array<{ role: string; content: string }>,
  opts?: {
    model?: string;
    maxTokens?: number;
    temperature?: number;
    systemPrompt?: string;
  },
) => Promise<{ text: string }>;
