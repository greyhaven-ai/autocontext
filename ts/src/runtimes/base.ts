/**
 * Agent runtime interfaces and types.
 * Port of autocontext/src/autocontext/runtimes/base.py
 */

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
  }): Promise<AgentOutput>;

  revise(opts: {
    prompt: string;
    previousOutput: string;
    feedback: string;
    system?: string;
  }): Promise<AgentOutput>;

  readonly name: string;
}
