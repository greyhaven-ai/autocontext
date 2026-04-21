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
  hashUserId,
  hashSessionId,
  loadInstallSalt,
} from "../../production-traces/sdk/hashing.js";

export const WRAPPED_SENTINEL = Symbol.for("autocontext.wrapped");

function _nowIso(): string {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

function _isAsyncClient(client: unknown): boolean {
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const { AsyncOpenAI } = require("openai") as { AsyncOpenAI: new (...args: unknown[]) => unknown };
    return client instanceof AsyncOpenAI;
  } catch {
    return false;
  }
}

function _resolveIdentity(perCall: Record<string, string> | null | undefined): Record<string, string> {
  let raw: Record<string, string> = {};
  if (perCall) {
    if (perCall["user_id"] != null) raw["user_id"] = perCall["user_id"];
    if (perCall["session_id"] != null) raw["session_id"] = perCall["session_id"];
  }
  if (Object.keys(raw).length === 0) {
    const ambient = currentSession();
    if (ambient.userId) raw["user_id"] = ambient.userId;
    if (ambient.sessionId) raw["session_id"] = ambient.sessionId;
  }
  if (Object.keys(raw).length === 0) return {};
  const salt = loadInstallSalt(".");
  const hashed: Record<string, string> = {};
  if (raw["user_id"]) hashed["user_id_hash"] = hashUserId(raw["user_id"], salt);
  if (raw["session_id"]) hashed["session_id_hash"] = hashSessionId(raw["session_id"], salt);
  return hashed;
}

function _sdkVersion(): string {
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const pkg = require("../../package.json") as { version?: string };
    return pkg.version ?? "0.0.0";
  } catch {
    return "0.0.0";
  }
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
    // Resolve version from the TS package
    let ver = "0.0.0";
    try {
      // Walk up to find package.json
      const { readFileSync, existsSync } = require("node:fs") as typeof import("node:fs");
      const { dirname, join, resolve } = require("node:path") as typeof import("node:path");
      const { fileURLToPath } = require("node:url") as typeof import("node:url");
      let dir = dirname(fileURLToPath(import.meta.url));
      for (let depth = 0; depth < 10; depth++) {
        const candidate = join(dir, "package.json");
        if (existsSync(candidate)) {
          const pkg = JSON.parse(readFileSync(candidate, "utf-8")) as { name?: string; version?: string };
          if (pkg.name === "autoctx" && typeof pkg.version === "string") {
            ver = pkg.version;
            break;
          }
        }
        const parent = dirname(dir);
        if (parent === dir) break;
        dir = parent;
      }
    } catch {
      // best-effort
    }
    return { emitter: "sdk", sdk: { name: "autocontext-ts", version: ver } };
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
    const identity = _resolveIdentity(perCall);
    const snapshot = buildRequestSnapshot({
      model: String(kwargs["model"] ?? ""),
      messages: (kwargs["messages"] as Array<Record<string, unknown>>) ?? [],
      extraKwargs: Object.fromEntries(
        Object.entries(kwargs).filter(([k]) => k !== "model" && k !== "messages"),
      ),
    });
    const startedAt = _nowIso();
    const startedMonotonic = Date.now();
    let response: unknown;
    try {
      const inner = this._inner as Record<string, { completions: { create: (k: unknown) => unknown } }>;
      response = inner["chat"]["completions"]["create"](kwargs);
    } catch (exc) {
      const endedAt = _nowIso();
      const latencyMs = Date.now() - startedMonotonic;
      const trace = buildFailureTrace({
        requestSnapshot: snapshot,
        identity,
        timing: { startedAt, endedAt, latencyMs },
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
        const endedAt = _nowIso();
        const latencyMs = Date.now() - startedMonotonic;
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
          timing: { startedAt, endedAt, latencyMs },
          env: this._env(),
          sourceInfo: this._sourceInfo(),
          traceId: ulid(),
        });
        this._sink.add(trace as unknown as Record<string, unknown>);
        return resp;
      },
      (exc: unknown) => {
        const endedAt = _nowIso();
        const latencyMs = Date.now() - startedMonotonic;
        const trace = buildFailureTrace({
          requestSnapshot: snapshot,
          identity,
          timing: { startedAt, endedAt, latencyMs },
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
    const identity = _resolveIdentity(perCall);
    const snapshot = buildRequestSnapshot({
      model: String(kwargs["model"] ?? ""),
      messages: (kwargs["messages"] as Array<Record<string, unknown>>) ?? [],
      extraKwargs: Object.fromEntries(
        Object.entries(kwargs).filter(([k]) => k !== "model" && k !== "messages"),
      ),
    });
    const startedAt = _nowIso();
    const startedMonotonic = Date.now();
    let resp: unknown;
    try {
      const inner = this._inner as Record<string, { completions: { create: (k: unknown) => Promise<unknown> } }>;
      resp = await inner["chat"]["completions"]["create"](kwargs);
    } catch (exc) {
      const endedAt = _nowIso();
      const latencyMs = Date.now() - startedMonotonic;
      const trace = buildFailureTrace({
        requestSnapshot: snapshot,
        identity,
        timing: { startedAt, endedAt, latencyMs },
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
    const endedAt = _nowIso();
    const latencyMs = Date.now() - startedMonotonic;
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
      timing: { startedAt, endedAt, latencyMs },
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
    const identity = _resolveIdentity(perCall);
    const snapshot = buildRequestSnapshot({
      model: String(kwargs["model"] ?? ""),
      messages: (kwargs["messages"] as Array<Record<string, unknown>>) ?? [],
      extraKwargs: Object.fromEntries(
        Object.entries(kwargs).filter(([k]) => k !== "model" && k !== "messages"),
      ),
    });
    const startedAt = _nowIso();
    const startedMonotonic = Date.now();
    const sink = this._sink;
    const env = this._env();
    const sourceInfo = this._sourceInfo();

    const inner = this._inner as Record<string, { completions: { create: (k: unknown) => unknown } }>;
    const streamResult = inner["chat"]["completions"]["create"](kwargs);

    // Import here to avoid circular imports
    const { AsyncStreamProxy } = require("./stream-proxy.js") as typeof import("./stream-proxy.js");

    // acc_ref avoids a cycle: proxy → on_finalize → acc_ref → proxy._accumulator
    const accRef: { accumulator: Record<string, unknown> | null } = { accumulator: null };

    const onFinalize = (outcome: Record<string, unknown>): void => {
      const endedAt = _nowIso();
      const latencyMs = Date.now() - startedMonotonic;
      const acc = accRef.accumulator ?? { usage: null, toolCalls: null };
      const trace = finalizeStreamingTrace({
        requestSnapshot: snapshot,
        identity,
        timing: { startedAt, endedAt, latencyMs },
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
    const identity = _resolveIdentity(perCall);
    const snapshot = buildRequestSnapshot({
      model: String(kwargs["model"] ?? ""),
      messages: (kwargs["messages"] as Array<Record<string, unknown>>) ?? [],
      extraKwargs: Object.fromEntries(
        Object.entries(kwargs).filter(([k]) => k !== "model" && k !== "messages"),
      ),
    });
    const startedAt = _nowIso();
    const startedMonotonic = Date.now();
    const sink = this._sink;
    const env = this._env();
    const sourceInfo = this._sourceInfo();

    const { AsyncStreamProxy } = require("./stream-proxy.js") as typeof import("./stream-proxy.js");

    const accRef: { accumulator: Record<string, unknown> | null } = { accumulator: null };

    const onFinalize = (outcome: Record<string, unknown>): void => {
      const endedAt = _nowIso();
      const latencyMs = Date.now() - startedMonotonic;
      const acc = accRef.accumulator ?? { usage: null, tool_calls: null };
      const trace = finalizeStreamingTrace({
        requestSnapshot: snapshot,
        identity,
        timing: { startedAt, endedAt, latencyMs },
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
    let innerStream = inner["chat"]["completions"]["create"](kwargs);
    if (innerStream && typeof (innerStream as { then?: unknown }).then === "function") {
      innerStream = await (innerStream as Promise<unknown>);
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
    const identity = _resolveIdentity(perCall);
    const model = String(kwargs["model"] ?? "");
    const snapshot = buildRequestSnapshot({
      model,
      messages: normalizedMessages,
      extraKwargs: Object.fromEntries(
        Object.entries(kwargs).filter(([k]) => k !== "model" && k !== "messages" && k !== "input"),
      ),
    });
    const startedAt = _nowIso();
    const startedMonotonic = Date.now();
    const inner = this._inner as Record<string, { create: (k: unknown) => unknown }>;
    const result = inner["responses"]["create"](kwargs);
    return (result as Promise<unknown>).then(
      (resp) => {
        const endedAt = _nowIso();
        const latencyMs = Date.now() - startedMonotonic;
        const r = resp as Record<string, unknown>;
        const usage = r["usage"] as Record<string, unknown> | null;
        const trace = buildSuccessTrace({
          requestSnapshot: snapshot,
          responseUsage: usage,
          responseToolCalls: null,
          identity,
          timing: { startedAt, endedAt, latencyMs },
          env: this._env(),
          sourceInfo: this._sourceInfo(),
          traceId: ulid(),
        });
        this._sink.add(trace as unknown as Record<string, unknown>);
        return resp;
      },
      (exc: unknown) => {
        const endedAt = _nowIso();
        const latencyMs = Date.now() - startedMonotonic;
        const trace = buildFailureTrace({
          requestSnapshot: snapshot,
          identity,
          timing: { startedAt, endedAt, latencyMs },
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
