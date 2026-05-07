/**
 * Provider bridge + RetryProvider (AC-345 Task 15).
 * Adapts AgentRuntime into LLMProvider interface with retry support.
 */

import type { CompletionResult, LLMProvider } from "../types/index.js";
import type { AgentRuntime } from "../runtimes/base.js";
import { RuntimeSessionAgentRuntime } from "../runtimes/runtime-session-agent.js";
import type { RuntimeCommandGrant } from "../runtimes/workspace-env.js";
import type { RuntimeSession } from "../session/runtime-session.js";

// ---------------------------------------------------------------------------
// RuntimeBridgeProvider — adapt AgentRuntime → LLMProvider
// ---------------------------------------------------------------------------

export class RuntimeBridgeProvider implements LLMProvider {
  readonly name = "runtime-bridge";
  #runtime: AgentRuntime;
  #model: string;

  constructor(runtime: AgentRuntime, model: string, opts: RuntimeBridgeProviderOpts = {}) {
    this.#runtime = opts.session
      ? new RuntimeSessionAgentRuntime({
          runtime,
          session: opts.session,
          role: opts.role ?? "runtime-bridge",
          cwd: opts.cwd,
          commands: opts.commands,
        })
      : runtime;
    this.#model = model;
  }

  get supportsConcurrentRequests() {
    return this.#runtime.supportsConcurrentRequests !== false;
  }

  defaultModel() {
    return this.#model;
  }

  close() {
    this.#runtime.close?.();
  }

  async complete(opts: {
    systemPrompt: string;
    userPrompt: string;
    model?: string;
    temperature?: number;
    maxTokens?: number;
  }): Promise<CompletionResult> {
    const output = await this.#runtime.generate({
      prompt: opts.userPrompt,
      system: opts.systemPrompt || undefined,
    });
    return {
      text: output.text,
      model: opts.model ?? this.#model,
      usage: {},
    };
  }
}

export interface RuntimeBridgeProviderOpts {
  session?: RuntimeSession;
  role?: string;
  cwd?: string;
  commands?: RuntimeCommandGrant[];
}

// ---------------------------------------------------------------------------
// RetryProvider — exponential backoff wrapper
// ---------------------------------------------------------------------------

export interface RetryOpts {
  maxRetries: number;
  baseDelay?: number;
  maxDelay?: number;
}

export class RetryProvider implements LLMProvider {
  readonly name: string;
  #inner: LLMProvider;
  #maxRetries: number;
  #baseDelay: number;
  #maxDelay: number;

  constructor(inner: LLMProvider, opts: RetryOpts) {
    this.#inner = inner;
    this.name = `retry(${inner.name})`;
    this.#maxRetries = opts.maxRetries;
    this.#baseDelay = opts.baseDelay ?? 250;
    this.#maxDelay = opts.maxDelay ?? 10_000;
  }

  get supportsConcurrentRequests() {
    return this.#inner.supportsConcurrentRequests !== false;
  }

  defaultModel() {
    return this.#inner.defaultModel();
  }

  close() {
    this.#inner.close?.();
  }

  async complete(opts: {
    systemPrompt: string;
    userPrompt: string;
    model?: string;
    temperature?: number;
    maxTokens?: number;
  }): Promise<CompletionResult> {
    let lastError: Error | undefined;
    for (let attempt = 0; attempt <= this.#maxRetries; attempt++) {
      try {
        return await this.#inner.complete(opts);
      } catch (err) {
        lastError = err instanceof Error ? err : new Error(String(err));
        if (attempt < this.#maxRetries) {
          const delay = Math.min(this.#baseDelay * 2 ** attempt, this.#maxDelay);
          await new Promise((r) => setTimeout(r, delay));
        }
      }
    }
    throw lastError!;
  }
}
