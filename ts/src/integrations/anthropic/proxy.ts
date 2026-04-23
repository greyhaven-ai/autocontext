/**
 * ClientProxy — Proxy-based wrapper around an Anthropic client.
 *
 * Intercepts .messages.create and .messages.stream. All other attribute
 * access passes through transparently. Mirror of Python _proxy.py for Anthropic.
 */
import { ulid } from "ulid";
import type { TraceSink } from "../_shared/sink.js";
import { currentSession } from "../_shared/session.js";
import { mapExceptionToReason } from "./taxonomy.js";
import {
  buildRequestSnapshot,
  buildSuccessTrace,
  buildFailureTrace,
  finalizeStreamingTrace,
  type RequestSnapshot,
} from "./trace-builder.js";
import { AnthropicStreamProxy, wrapHelperStream } from "./stream-proxy.js";
import {
  buildProviderSourceInfo,
  finishInvocationTiming,
  resolveProviderIdentity,
  startInvocationClock,
} from "../_shared/proxy-runtime.js";
import type { ContentBlock } from "./content.js";

export const WRAPPED_SENTINEL = Symbol.for("autocontext.wrapped");

function _responseUsageAndContent(resp: Record<string, unknown>): {
  usage: Record<string, unknown> | null;
  content: ContentBlock[];
  stopReason: string | null;
} {
  return {
    usage: (resp["usage"] as Record<string, unknown>) ?? null,
    content: ((resp["content"] as Array<Record<string, unknown>>) ?? []) as ContentBlock[],
    stopReason: (resp["stop_reason"] as string) ?? null,
  };
}

export class ClientProxy {
  readonly _inner: unknown;
  readonly _sink: TraceSink;
  readonly _appId: string;
  readonly _environmentTag: string;

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
  }

  _sourceInfo(): { emitter: string; sdk: { name: string; version: string } } {
    return buildProviderSourceInfo(import.meta.url);
  }

  _env(): { environmentTag: string; appId: string } {
    return { environmentTag: this._environmentTag, appId: this._appId };
  }

  async _invokeNonStreaming(kwargs: Record<string, unknown>): Promise<unknown> {
    const perCall = kwargs["autocontext"] as Record<string, string> | null;
    delete kwargs["autocontext"];
    const identity = resolveProviderIdentity(perCall, currentSession());
    const snapshot: RequestSnapshot = buildRequestSnapshot({
      model: String(kwargs["model"] ?? ""),
      messages: (kwargs["messages"] as Array<Record<string, unknown>>) ?? [],
      extraKwargs: Object.fromEntries(
        Object.entries(kwargs).filter(([k]) => k !== "model" && k !== "messages"),
      ),
    });
    const clock = startInvocationClock();
    let resp: unknown;
    try {
      const inner = this._inner as {
        messages: { create: (k: unknown) => Promise<unknown> };
      };
      resp = await inner.messages.create(kwargs);
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
    const usage = (r["usage"] as Record<string, unknown>) ?? null;
    const content = (r["content"] as Array<Record<string, unknown>>) ?? [];
    const stopReason = (r["stop_reason"] as string) ?? null;
    const trace = buildSuccessTrace({
      requestSnapshot: snapshot,
      responseContent: content as unknown as ContentBlock[],
      responseUsage: usage,
      responseStopReason: stopReason,
      identity,
      timing,
      env: this._env(),
      sourceInfo: this._sourceInfo(),
      traceId: ulid(),
    });
    this._sink.add(trace as unknown as Record<string, unknown>);
    return resp;
  }

  _invokeStreaming(kwargs: Record<string, unknown>): AnthropicStreamProxy {
    const perCall = kwargs["autocontext"] as Record<string, string> | null;
    delete kwargs["autocontext"];
    const identity = resolveProviderIdentity(perCall, currentSession());
    const snapshot: RequestSnapshot = buildRequestSnapshot({
      model: String(kwargs["model"] ?? ""),
      messages: (kwargs["messages"] as Array<Record<string, unknown>>) ?? [],
      extraKwargs: Object.fromEntries(
        Object.entries(kwargs).filter(([k]) => k !== "model" && k !== "messages"),
      ),
    });
    const clock = startInvocationClock();
    const inner = this._inner as {
      messages: { create: (k: unknown) => unknown };
    };
    const rawStream = inner.messages.create(kwargs);
    const sink = this._sink;
    const env = this._env();
    const sourceInfo = this._sourceInfo();

    const onFinalize = (
      blocks: Map<number, import("./trace-builder.js").AccumulatedBlock>,
      usage: Record<string, unknown> | null,
      stopReason: string | null,
      outcome: Record<string, unknown>,
    ): void => {
      const timing = finishInvocationTiming(clock);
      const trace = finalizeStreamingTrace({
        requestSnapshot: snapshot,
        identity,
        timing,
        env,
        sourceInfo,
        traceId: ulid(),
        accumulatedContentBlocks: blocks,
        accumulatedUsage: usage,
        accumulatedStopReason: stopReason,
        outcome,
      });
      sink.add(trace as unknown as Record<string, unknown>);
    };

    return new AnthropicStreamProxy({ innerStream: rawStream, onFinalize });
  }

  _invokeHelperStreaming(kwargs: Record<string, unknown>): unknown {
    const perCall = kwargs["autocontext"] as Record<string, string> | null;
    delete kwargs["autocontext"];
    const identity = resolveProviderIdentity(perCall, currentSession());
    const snapshot: RequestSnapshot = buildRequestSnapshot({
      model: String(kwargs["model"] ?? ""),
      messages: (kwargs["messages"] as Array<Record<string, unknown>>) ?? [],
      extraKwargs: Object.fromEntries(
        Object.entries(kwargs).filter(([k]) => k !== "model" && k !== "messages"),
      ),
    });
    const clock = startInvocationClock();
    const inner = this._inner as {
      messages: { stream: (k: unknown) => unknown };
    };
    const helper = inner.messages.stream(kwargs);
    const sink = this._sink;
    const env = this._env();
    const sourceInfo = this._sourceInfo();

    return wrapHelperStream({
      innerHelper: helper,
      onFinalize: (message, outcome) => {
        const timing = finishInvocationTiming(clock);
        const { usage, content, stopReason } = _responseUsageAndContent(message);
        const trace = buildSuccessTrace({
          requestSnapshot: snapshot,
          responseContent: content,
          responseUsage: usage,
          responseStopReason: stopReason,
          identity,
          timing,
          env,
          sourceInfo,
          traceId: ulid(),
        });
        sink.add(
          { ...trace, outcome: outcome as typeof trace.outcome } as unknown as Record<string, unknown>,
        );
      },
      onFailure: (exc) => {
        const timing = finishInvocationTiming(clock);
        const trace = buildFailureTrace({
          requestSnapshot: snapshot,
          identity,
          timing,
          env,
          sourceInfo,
          traceId: ulid(),
          reasonKey: mapExceptionToReason(exc),
          errorMessage: String(exc),
          stack: exc instanceof Error ? (exc.stack ?? null) : null,
        });
        sink.add(trace as unknown as Record<string, unknown>);
      },
    });
  }
}
