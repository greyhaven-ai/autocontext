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

// ---------------------------------------------------------------------------
// Inferred TypeScript types
// ---------------------------------------------------------------------------

export type ReplCommand = z.infer<typeof ReplCommandSchema>;
export type ReplResult = z.infer<typeof ReplResultSchema>;
export type ExecutionRecord = z.infer<typeof ExecutionRecordSchema>;
export type RlmContext = z.infer<typeof RlmContextSchema>;

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
