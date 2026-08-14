/**
 * Provider-owned runtime bridge + RetryProvider (AC-345 Task 15, AC-946).
 * Adapts AgentRuntime into LLMProvider interface with retry support.
 */

import type { CompletionResult, LLMProvider, ThinkingCompletionOptions } from "../types/index.js";
import { ProviderError } from "../types/index.js";
import { addCompletionUsage, completeWithThinkingFallback } from "./thinking.js";
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

  get supportsThinkingStream() {
    return false;
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

  get supportsThinkingStream() {
    return this.#inner.supportsThinkingStream === true;
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
    return this.#withRetry(() => this.#inner.complete(opts));
  }

  async completeWithThinking(opts: ThinkingCompletionOptions): Promise<CompletionResult> {
    return this.#withRetry(() => completeWithThinkingFallback(this.#inner, opts));
  }

  async #withRetry(operation: () => Promise<CompletionResult>): Promise<CompletionResult> {
    let lastError: Error | undefined;
    const retryUsage: Record<string, number> = {};
    for (let attempt = 0; attempt <= this.#maxRetries; attempt++) {
      try {
        const result = await operation();
        const usage = { ...result.usage };
        addCompletionUsage(usage, retryUsage);
        return { ...result, usage };
      } catch (err) {
        lastError = err instanceof Error ? err : new Error(String(err));
        if (err instanceof ProviderError) addCompletionUsage(retryUsage, err.usage);
        if (attempt < this.#maxRetries) {
          const delay = Math.min(this.#baseDelay * 2 ** attempt, this.#maxDelay);
          await new Promise((r) => setTimeout(r, delay));
        }
      }
    }
    if (lastError instanceof ProviderError) lastError.usage = retryUsage;
    throw lastError!;
  }
}
