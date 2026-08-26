/**
 * Provider-owned runtime bridge + RetryProvider (AC-345 Task 15, AC-946).
 * Adapts AgentRuntime into LLMProvider interface with retry support.
 */

import type {
  CompletionOptions,
  CompletionResult,
  LLMProvider,
  ProviderIsolationPolicy,
  ThinkingCompletionOptions,
} from "../types/index.js";
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
  readonly evaluatorIdentity: string | undefined;
  readonly createIsolatedProvider?: (policy?: ProviderIsolationPolicy) => LLMProvider;
  #runtime: AgentRuntime;
  #model: string;
  #createModelProvider: ((model: string) => LLMProvider) | undefined;

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
    this.evaluatorIdentity = opts.evaluatorIdentity;
    this.createIsolatedProvider = opts.createIsolatedProvider;
    this.#createModelProvider = opts.createModelProvider;
  }

  get supportsConcurrentRequests() {
    return this.#runtime.supportsConcurrentRequests !== false;
  }

  get supportsThinkingStream() {
    return false;
  }

  supportsImageAttachments(): boolean {
    return false;
  }

  defaultModel() {
    return this.#model;
  }

  close() {
    this.#runtime.close?.();
  }

  async complete(opts: CompletionOptions): Promise<CompletionResult> {
    if (opts.imageAttachments?.length) {
      throw new ProviderError("Runtime-backed providers do not support image attachments");
    }
    const requestedModel = opts.model ?? this.#model;
    if (requestedModel !== this.#model) {
      if (!this.#createModelProvider) {
        throw new ProviderError(
          `Runtime-backed provider configured for ${JSON.stringify(this.#model)} cannot honor requested model ${JSON.stringify(requestedModel)}`,
        );
      }
      const modelProvider = this.#createModelProvider(requestedModel);
      if (!modelProvider || modelProvider === this) {
        throw new ProviderError(
          `Runtime-backed provider could not create an owned runtime for requested model ${JSON.stringify(requestedModel)}`,
        );
      }
      try {
        return await modelProvider.complete({ ...opts, model: requestedModel });
      } finally {
        modelProvider.close?.();
      }
    }
    const output = await this.#runtime.generate({
      prompt: opts.userPrompt,
      system: opts.systemPrompt || undefined,
      ...(opts.promptVisibility ? { promptVisibility: opts.promptVisibility } : {}),
    });
    return {
      text: output.text,
      model: requestedModel,
      usage: {},
    };
  }
}

export interface RuntimeBridgeProviderOpts {
  session?: RuntimeSession;
  role?: string;
  cwd?: string;
  commands?: RuntimeCommandGrant[];
  createIsolatedProvider?: (policy?: ProviderIsolationPolicy) => LLMProvider;
  /** Stable backend identity used for evaluator epochs; `name` stays API-compatible. */
  evaluatorIdentity?: string;
  /** Create a fresh owned runtime that actually executes the requested model. */
  createModelProvider?: (model: string) => LLMProvider;
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
  readonly createIsolatedProvider?: (policy?: ProviderIsolationPolicy) => LLMProvider;
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
    if (inner.createIsolatedProvider) {
      this.createIsolatedProvider = (policy) => {
        const isolatedInner = inner.createIsolatedProvider!(policy);
        if (isolatedInner === inner) return this;
        return new RetryProvider(isolatedInner, {
          maxRetries: this.#maxRetries,
          baseDelay: this.#baseDelay,
          maxDelay: this.#maxDelay,
        });
      };
    }
  }

  get supportsConcurrentRequests() {
    return this.#inner.supportsConcurrentRequests !== false;
  }

  get isStatelessNoToolsProvider() {
    return this.#inner.isStatelessNoToolsProvider === true;
  }

  get evaluatorIdentity() {
    return this.#inner.evaluatorIdentity;
  }

  get supportsThinkingStream() {
    return this.#inner.supportsThinkingStream === true;
  }

  supportsImageAttachments(model?: string): boolean {
    return this.#inner.supportsImageAttachments?.(model ?? this.#inner.defaultModel()) === true;
  }

  defaultModel() {
    return this.#inner.defaultModel();
  }

  close() {
    this.#inner.close?.();
  }

  async complete(opts: CompletionOptions): Promise<CompletionResult> {
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
