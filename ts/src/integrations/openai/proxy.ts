/**
 * ClientProxy — Proxy-based wrapper around an OpenAI client.
 *
 * Intercepts .chat.completions.create / .responses.create. All other
 * attribute access passes through transparently. Spec §4.1 + §6.2.
 * Mirror of Python ``_proxy.py``.
 */
import { ulid } from "ulid";
import type { TraceSink } from "./sink.js";
import { currentSession } from "./session.js";
import { mapExceptionToReason } from "./taxonomy.js";
import {
  buildRequestSnapshot,
  buildSuccessTrace,
  buildFailureTrace,
  finalizeStreamingTrace,
  type RequestSnapshot,
} from "./trace-builder.js";
import {
  buildProviderSourceInfo,
  finishInvocationTiming,
  resolveProviderIdentity,
  startInvocationClock,
} from "../_shared/proxy-runtime.js";
import { AsyncStreamProxy } from "./stream-proxy.js";

export const WRAPPED_SENTINEL = Symbol.for("autocontext.wrapped");

function _isAsyncClient(client: unknown): boolean {
  // Check by class name to avoid ESM require() issues with the openai package
  const className = (client as object)?.constructor?.name ?? "";
  return className.startsWith("Async");
}

export class ClientProxy {
  private readonly _inner: unknown;
  private readonly _sink: TraceSink;
  private readonly _appId: string;
  private readonly _environmentTag: string;
  private readonly _isAsync: boolean;

  constructor(opts: {
    inner: unknown;
    sink: TraceSink;
    appId: string;
    environmentTag: string;
  }) {
    this._inner = opts.inner;
    this._sink = opts.sink;
    this._appId = opts.appId;
    this._environmentTag = opts.environmentTag;
    this._isAsync = _isAsyncClient(opts.inner);
  }

  _sourceInfo(): { emitter: string; sdk: { name: string; version: string } } {
    return buildProviderSourceInfo(import.meta.url);
  }

  _env(): { environmentTag: string; appId: string } {
    return { environmentTag: this._environmentTag, appId: this._appId };
  }

  _invokeChatCompletionsCreate(kwargs: Record<string, unknown>): unknown {
    if (kwargs["stream"]) {
      if (this._isAsync) return this._invokeStreamingAsync(kwargs);
      return this._invokeStreaming(kwargs);
    }
    if (this._isAsync) return this._invokeNonStreamingAsync(kwargs);
    return this._invokeNonStreaming(kwargs);
  }

  _invokeNonStreaming(kwargs: Record<string, unknown>): unknown {
    const perCall = kwargs["autocontext"] as Record<string, string> | null;
    delete kwargs["autocontext"];
    const identity = resolveProviderIdentity(perCall, currentSession());
    const snapshot = buildRequestSnapshot({
      model: String(kwargs["model"] ?? ""),
      messages: (kwargs["messages"] as Array<Record<string, unknown>>) ?? [],
      extraKwargs: Object.fromEntries(
        Object.entries(kwargs).filter(([k]) => k !== "model" && k !== "messages"),
      ),
    });
    const clock = startInvocationClock();
    let response: unknown;
    try {
      const inner = this._inner as Record<string, { completions: { create: (k: unknown) => unknown } }>;
      response = inner["chat"]["completions"]["create"](kwargs);
    } catch (exc) {
      const timing = finishInvocationTiming(clock);
      const trace = buildFailureTrace({
        requestSnapshot: snapshot,
        identity,
        timing,
        env: this._env(),
        sourceInfo: this._sourceInfo(),
        traceId: ulid(),
        reasonKey: mapExceptionToReason(exc),
        errorMessage: String(exc),
        stack: exc instanceof Error ? (exc.stack ?? null) : null,
      });
      this._sink.add(trace as unknown as Record<string, unknown>);
      throw exc;
    }
    // Response is a Promise for async, but for sync OpenAI this is direct
    return (response as Promise<unknown>).then(
      (resp) => {
        const timing = finishInvocationTiming(clock);
        const r = resp as Record<string, unknown>;
        const usage = r["usage"] as Record<string, unknown> | null;
        let toolCalls: Array<Record<string, unknown>> | null = null;
        const choices = r["choices"] as Array<Record<string, unknown>> | undefined;
        if (choices && choices.length > 0) {
          const msg = (choices[0]!["message"] as Record<string, unknown>);
          const tcs = msg?.["tool_calls"] as Array<Record<string, unknown>> | null;
          if (tcs && tcs.length > 0) toolCalls = tcs;
        }
        const trace = buildSuccessTrace({
          requestSnapshot: snapshot,
          responseUsage: usage,
          responseToolCalls: toolCalls,
          identity,
          timing,
          env: this._env(),
          sourceInfo: this._sourceInfo(),
          traceId: ulid(),
        });
        this._sink.add(trace as unknown as Record<string, unknown>);
        return resp;
      },
      (exc: unknown) => {
        const timing = finishInvocationTiming(clock);
        const trace = buildFailureTrace({
          requestSnapshot: snapshot,
          identity,
          timing,
          env: this._env(),
          sourceInfo: this._sourceInfo(),
          traceId: ulid(),
          reasonKey: mapExceptionToReason(exc),
          errorMessage: String(exc),
          stack: exc instanceof Error ? (exc.stack ?? null) : null,
        });
        this._sink.add(trace as unknown as Record<string, unknown>);
        throw exc;
      },
    );
  }

  async _invokeNonStreamingAsync(kwargs: Record<string, unknown>): Promise<unknown> {
    const perCall = kwargs["autocontext"] as Record<string, string> | null;
    delete kwargs["autocontext"];
    const identity = resolveProviderIdentity(perCall, currentSession());
    const snapshot = buildRequestSnapshot({
      model: String(kwargs["model"] ?? ""),
      messages: (kwargs["messages"] as Array<Record<string, unknown>>) ?? [],
      extraKwargs: Object.fromEntries(
        Object.entries(kwargs).filter(([k]) => k !== "model" && k !== "messages"),
      ),
    });
    const clock = startInvocationClock();
    let resp: unknown;
    try {
      const inner = this._inner as Record<string, { completions: { create: (k: unknown) => Promise<unknown> } }>;
      resp = await inner["chat"]["completions"]["create"](kwargs);
    } catch (exc) {
      const timing = finishInvocationTiming(clock);
      const trace = buildFailureTrace({
        requestSnapshot: snapshot,
        identity,
        timing,
        env: this._env(),
        sourceInfo: this._sourceInfo(),
        traceId: ulid(),
        reasonKey: mapExceptionToReason(exc),
        errorMessage: String(exc),
        stack: exc instanceof Error ? (exc.stack ?? null) : null,
      });
      this._sink.add(trace as unknown as Record<string, unknown>);
      throw exc;
    }
    const timing = finishInvocationTiming(clock);
    const r = resp as Record<string, unknown>;
    const usage = r["usage"] as Record<string, unknown> | null;
    let toolCalls: Array<Record<string, unknown>> | null = null;
    const choices = r["choices"] as Array<Record<string, unknown>> | undefined;
    if (choices && choices.length > 0) {
      const msg = choices[0]!["message"] as Record<string, unknown>;
      const tcs = msg?.["tool_calls"] as Array<Record<string, unknown>> | null;
      if (tcs && tcs.length > 0) toolCalls = tcs;
    }
    const trace = buildSuccessTrace({
      requestSnapshot: snapshot,
      responseUsage: usage,
      responseToolCalls: toolCalls,
      identity,
      timing,
      env: this._env(),
      sourceInfo: this._sourceInfo(),
      traceId: ulid(),
    });
    this._sink.add(trace as unknown as Record<string, unknown>);
    return resp;
  }

  _invokeStreaming(kwargs: Record<string, unknown>): unknown {
    const perCall = kwargs["autocontext"] as Record<string, string> | null;
    delete kwargs["autocontext"];
    // Auto-inject stream_options.include_usage = true if absent (not set by caller)
    const streamOpts = Object.assign({}, (kwargs["stream_options"] as Record<string, unknown>) ?? {});
    if (!("include_usage" in streamOpts)) {
      streamOpts["include_usage"] = true;
      kwargs["stream_options"] = streamOpts;
    }
    const identity = resolveProviderIdentity(perCall, currentSession());
    const snapshot = buildRequestSnapshot({
      model: String(kwargs["model"] ?? ""),
      messages: (kwargs["messages"] as Array<Record<string, unknown>>) ?? [],
      extraKwargs: Object.fromEntries(
        Object.entries(kwargs).filter(([k]) => k !== "model" && k !== "messages"),
      ),
    });
    const clock = startInvocationClock();
    const sink = this._sink;
    const env = this._env();
    const sourceInfo = this._sourceInfo();

    const inner = this._inner as Record<string, { completions: { create: (k: unknown) => unknown } }>;
    const streamResult = inner["chat"]["completions"]["create"](kwargs);

    // acc_ref avoids a cycle: proxy → on_finalize → acc_ref → proxy._accumulator
    const accRef: { accumulator: Record<string, unknown> | null } = { accumulator: null };

    const onFinalize = (outcome: Record<string, unknown>): void => {
      const timing = finishInvocationTiming(clock);
      const acc = accRef.accumulator ?? { usage: null, toolCalls: null };
      const trace = finalizeStreamingTrace({
        requestSnapshot: snapshot,
        identity,
        timing,
        env,
        sourceInfo,
        traceId: ulid(),
        accumulatedUsage: (acc["usage"] as Record<string, unknown>) ?? null,
        accumulatedToolCalls: (acc["tool_calls"] as Array<Record<string, unknown>>) ?? null,
        outcome,
      });
      sink.add(trace as unknown as Record<string, unknown>);
    };

    // The proxy wraps the stream promise
    const proxy = new AsyncStreamProxy({ innerStream: streamResult, onFinalize });
    accRef.accumulator = proxy._accumulator;
    return proxy;
  }

  async _invokeStreamingAsync(kwargs: Record<string, unknown>): Promise<unknown> {
    const perCall = kwargs["autocontext"] as Record<string, string> | null;
    delete kwargs["autocontext"];
    // Auto-inject stream_options.include_usage = true if absent
    const streamOpts = Object.assign({}, (kwargs["stream_options"] as Record<string, unknown>) ?? {});
    if (!("include_usage" in streamOpts)) {
      streamOpts["include_usage"] = true;
      kwargs["stream_options"] = streamOpts;
    }
    const identity = resolveProviderIdentity(perCall, currentSession());
    const snapshot = buildRequestSnapshot({
      model: String(kwargs["model"] ?? ""),
      messages: (kwargs["messages"] as Array<Record<string, unknown>>) ?? [],
      extraKwargs: Object.fromEntries(
        Object.entries(kwargs).filter(([k]) => k !== "model" && k !== "messages"),
      ),
    });
    const clock = startInvocationClock();
    const sink = this._sink;
    const env = this._env();
    const sourceInfo = this._sourceInfo();

    const accRef: { accumulator: Record<string, unknown> | null } = { accumulator: null };

    const onFinalize = (outcome: Record<string, unknown>): void => {
      const timing = finishInvocationTiming(clock);
      const acc = accRef.accumulator ?? { usage: null, tool_calls: null };
      const trace = finalizeStreamingTrace({
        requestSnapshot: snapshot,
        identity,
        timing,
        env,
        sourceInfo,
        traceId: ulid(),
        accumulatedUsage: (acc["usage"] as Record<string, unknown>) ?? null,
        accumulatedToolCalls: (acc["tool_calls"] as Array<Record<string, unknown>>) ?? null,
        outcome,
      });
      sink.add(trace as unknown as Record<string, unknown>);
    };

    const inner = this._inner as Record<string, { completions: { create: (k: unknown) => Promise<unknown> } }>;
    const rawStream: unknown = inner["chat"]["completions"]["create"](kwargs);
    let innerStream: unknown = rawStream;
    if (rawStream && typeof (rawStream as { then?: unknown }).then === "function") {
      innerStream = await (rawStream as Promise<unknown>);
    }

    const proxy = new AsyncStreamProxy({ innerStream, onFinalize });
    accRef.accumulator = proxy._accumulator;
    return proxy;
  }

  _invokeResponsesCreate(
    kwargs: Record<string, unknown>,
    normalizedMessages: Array<Record<string, unknown>>,
  ): unknown {
    const perCall = kwargs["autocontext"] as Record<string, string> | null;
    delete kwargs["autocontext"];
    const identity = resolveProviderIdentity(perCall, currentSession());
    const model = String(kwargs["model"] ?? "");
    const snapshot = buildRequestSnapshot({
      model,
      messages: normalizedMessages,
      extraKwargs: Object.fromEntries(
        Object.entries(kwargs).filter(([k]) => k !== "model" && k !== "messages" && k !== "input"),
      ),
    });
    const clock = startInvocationClock();
    const inner = this._inner as Record<string, { create: (k: unknown) => unknown }>;
    const result = inner["responses"]["create"](kwargs);
    return (result as Promise<unknown>).then(
      (resp) => {
        const timing = finishInvocationTiming(clock);
        const r = resp as Record<string, unknown>;
        const usage = r["usage"] as Record<string, unknown> | null;
        const trace = buildSuccessTrace({
          requestSnapshot: snapshot,
          responseUsage: usage,
          responseToolCalls: null,
          identity,
          timing,
          env: this._env(),
          sourceInfo: this._sourceInfo(),
          traceId: ulid(),
        });
        this._sink.add(trace as unknown as Record<string, unknown>);
        return resp;
      },
      (exc: unknown) => {
        const timing = finishInvocationTiming(clock);
        const trace = buildFailureTrace({
          requestSnapshot: snapshot,
          identity,
          timing,
          env: this._env(),
          sourceInfo: this._sourceInfo(),
          traceId: ulid(),
          reasonKey: mapExceptionToReason(exc),
          errorMessage: String(exc),
          stack: exc instanceof Error ? (exc.stack ?? null) : null,
        });
        this._sink.add(trace as unknown as Record<string, unknown>);
        throw exc;
      },
    );
  }
}
