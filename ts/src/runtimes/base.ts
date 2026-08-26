/**
 * Agent runtime interfaces and types.
 * Port of autocontext/src/autocontext/runtimes/base.py
 */

import type { PromptVisibility } from "../types/index.js";

export interface AgentOutput {
  text: string;
  structured?: Record<string, unknown>;
  costUsd?: number;
  model?: string;
  sessionId?: string;
  metadata?: Record<string, unknown>;
}

export interface AgentRuntime {
  generate(opts: {
    prompt: string;
    system?: string;
    schema?: Record<string, unknown>;
    promptVisibility?: PromptVisibility;
  }): Promise<AgentOutput>;

  revise(opts: {
    prompt: string;
    previousOutput: string;
    feedback: string;
    system?: string;
    promptVisibility?: PromptVisibility;
  }): Promise<AgentOutput>;

  close?(): void;

  readonly supportsConcurrentRequests?: boolean;

  readonly name: string;
}
